"""
Five-layer cascade controller.

Cascade discipline: attempt the cheapest, most deterministic layer
first.  Escalate to the next layer only when the current layer
explicitly signals it cannot complete the task.  Every escalation is
logged with a structured reason so the decision chain is fully auditable.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Layer 1  ──  Direct deterministic API / CLI
               No UI automation.  When the OS or app exposes a direct
               CLI, API, or file-I/O path, take it.

  Layer 2a ──  OS hotkeys / deterministic UI automation
               cua-driver AX tree + element_index addressing.
               Deterministic key sequences.  No LLM.  No vision.

  Layer 2b ──  Accessibility tree / Page DOM / cheap text LLM
               Electron `page` tool (CDP) or AX tree with a small text
               LLM for parsing.  No images, no screenshots.

  Layer 3  ──  Vision-based perception and action
               Only when AX is empty (canvas, game, opaque Electron).
               Screenshot → multimodal LLM → pixel coordinates → click.

  Layer 4  ──  Human confirmation / safe stop
               Automated cascade exhausted.  Surface a clear description
               of the needed action; wait for operator approval.

  Layer 5  ──  Memory, logging, replay, trajectory evidence
               Always-on alongside every other layer.  Not a decision
               point — wraps every run with start_recording / metadata.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("cascade")


# ── Layer taxonomy ────────────────────────────────────────────────────────────

class Layer(str, Enum):
    L1_CLI    = "1"    # Direct API/CLI
    L2A_AX    = "2a"   # OS hotkeys / AX automation
    L2B_PAGE  = "2b"   # Electron page DOM / cheap text LLM
    L3_VISION = "3"    # Vision-based perception
    L4_HUMAN  = "4"    # Human confirmation / safe stop
    L5_MEMORY = "5"    # Always-on logging / replay (not a decision layer)


LAYER_DESCRIPTIONS = {
    Layer.L1_CLI:    "Direct deterministic API/CLI — no UI automation",
    Layer.L2A_AX:    "OS hotkeys + AX tree element_index — no vision, no LLM",
    Layer.L2B_PAGE:  "Electron page DOM / CDP / cheap text LLM — no images",
    Layer.L3_VISION: "Vision-based perception — screenshot + multimodal LLM",
    Layer.L4_HUMAN:  "Human confirmation / safe stop",
    Layer.L5_MEMORY: "Memory, logging, replay, trajectory evidence (always-on)",
}

# Numeric order for max_layer comparisons (L5 is out-of-band).
_LAYER_ORDER: dict[Layer, int] = {
    Layer.L1_CLI:    1,
    Layer.L2A_AX:    2,
    Layer.L2B_PAGE:  3,
    Layer.L3_VISION: 4,
    Layer.L4_HUMAN:  5,
    Layer.L5_MEMORY: 0,
}


# ── Control-flow signals ──────────────────────────────────────────────────────

class EscalateToNextLayer(Exception):
    """
    Raise inside a layer handler to signal: "I cannot do this, try the
    next layer."  The cascade controller catches it, logs the reason, and
    attempts the next configured layer.
    """
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LayerBlocked(RuntimeError):
    """
    Raised when a task tries to escalate past its declared `max_layer`.
    This is the guardrail that prevents e.g. Task 1 from ever calling
    vision, even if the AX layer raises EscalateToNextLayer.
    """


class LayerFailed(RuntimeError):
    """Raised for unrecoverable errors inside a layer (not an escalation)."""


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class EscalationRecord:
    from_layer:  str
    to_layer:    str
    reason:      str
    elapsed_s:   float


@dataclass
class CascadeResult:
    task_name:   str
    layer_used:  Layer
    success:     bool
    output:      Any         = None
    error:       str         = ""
    escalations: list[EscalationRecord] = field(default_factory=list)
    elapsed_s:   float       = 0.0

    def summary(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        esc_str = (
            " → ".join(e.from_layer for e in self.escalations) + f" → {self.layer_used.value}"
            if self.escalations else self.layer_used.value
        )
        return (
            f"[CASCADE] task={self.task_name}  "
            f"status={status}  layer_path={esc_str}  "
            f"elapsed={self.elapsed_s:.2f}s"
        )


# ── Controller ────────────────────────────────────────────────────────────────

class CascadeController:
    """
    Runs a sequence of (Layer, reason, fn) steps, escalating through
    them in order.

    Parameters
    ----------
    task_name : str
        Human-readable task name for logging and CascadeResult.
    max_layer : Layer
        Hard ceiling on which layer may be attempted.  Any escalation
        past this layer raises LayerBlocked instead of trying the next
        step — the primary guardrail for Task 1 (no vision).

    Usage
    -----
        cascade = CascadeController("calculator", max_layer=Layer.L2A_AX)
        result = cascade.run([
            (Layer.L1_CLI,  "no direct CLI for OS Calculator",  fn_layer1),
            (Layer.L2A_AX,  "click buttons via AX element_index", fn_layer2a),
        ])
        print(result.summary())
    """

    def __init__(
        self,
        task_name: str,
        max_layer: Layer = Layer.L3_VISION,
    ):
        self.task_name  = task_name
        self.max_layer  = max_layer
        self._escalations: list[EscalationRecord] = []

    def run(
        self,
        steps: list[tuple[Layer, str, Callable[[], Any]]],
    ) -> CascadeResult:
        """
        Execute each step in order.

        - fn() → any       → success, return CascadeResult(success=True)
        - fn() raises EscalateToNextLayer → log escalation, try next step
        - fn() raises LayerBlocked  → re-raise (hard ceiling hit)
        - fn() raises other         → re-raise (unrecoverable error)

        If all steps are exhausted without success, returns
        CascadeResult(success=False, layer_used=L4_HUMAN).
        """
        run_start = time.time()
        self._escalations = []
        last_reason = "all layers exhausted"

        for idx, (layer, reason, fn) in enumerate(steps):
            self._check_ceiling(layer, reason)

            logger.info(
                "─" * 60 + "\n"
                f"[CASCADE] Attempting Layer {layer.value}: "
                f"{LAYER_DESCRIPTIONS[layer]}\n"
                f"           Task: {self.task_name}  Reason: {reason}"
            )

            step_start = time.time()
            try:
                output = fn()
                step_elapsed = time.time() - step_start
                logger.info(
                    f"[CASCADE] Layer {layer.value} ✓ SUCCESS  "
                    f"({step_elapsed:.2f}s)"
                )
                result = CascadeResult(
                    task_name=self.task_name,
                    layer_used=layer,
                    success=True,
                    output=output,
                    escalations=list(self._escalations),
                    elapsed_s=time.time() - run_start,
                )
                logger.info(result.summary())
                return result

            except EscalateToNextLayer as exc:
                step_elapsed = time.time() - step_start
                next_layer = steps[idx + 1][0] if idx + 1 < len(steps) else Layer.L4_HUMAN
                rec = EscalationRecord(
                    from_layer=layer.value,
                    to_layer=next_layer.value,
                    reason=exc.reason,
                    elapsed_s=step_elapsed,
                )
                self._escalations.append(rec)
                last_reason = exc.reason
                logger.warning(
                    f"[CASCADE] Layer {layer.value} → escalating to "
                    f"Layer {next_layer.value}: {exc.reason}"
                )

            except LayerBlocked:
                raise

            except Exception as exc:
                step_elapsed = time.time() - step_start
                logger.error(
                    f"[CASCADE] Layer {layer.value} ✗ UNRECOVERABLE "
                    f"after {step_elapsed:.2f}s: {exc}"
                )
                raise

        # All layers exhausted → human fallback
        result = CascadeResult(
            task_name=self.task_name,
            layer_used=Layer.L4_HUMAN,
            success=False,
            error=f"All layers exhausted. Last reason: {last_reason}",
            escalations=list(self._escalations),
            elapsed_s=time.time() - run_start,
        )
        logger.error(result.summary())
        return result

    def _check_ceiling(self, layer: Layer, reason: str) -> None:
        """Raise LayerBlocked if `layer` exceeds `max_layer`."""
        if _LAYER_ORDER.get(layer, 99) > _LAYER_ORDER.get(self.max_layer, 99):
            msg = (
                f"[CASCADE GUARDRAIL] Task '{self.task_name}' attempted "
                f"Layer {layer.value} ({LAYER_DESCRIPTIONS[layer]}) "
                f"but max_layer={self.max_layer.value}. "
                f"Reason requested: '{reason}'. BLOCKED."
            )
            logger.error(msg)
            raise LayerBlocked(msg)
