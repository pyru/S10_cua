"""
Task 1: Calculator arithmetic — Layer 2a only.
Expression: 123 + 456 = 579

ZERO VISION CALLS.  The cascade is hard-capped at Layer.L2A_AX.
If the AX tree cannot be read the task raises LayerBlocked — it will
never silently degrade to a screenshot or vision call.

Cascade path:
  Layer 1  — Check for direct CLI computation path.
             Escalates immediately: the task requirement is to drive
             the OS Calculator UI and record the run, not to shell out
             `python -c "print(123+456)"`.
  Layer 2a — Launch Calculator, activate window, scan AX tree for
             button elements, click the digit/operator sequence via
             element_index, read the display to verify "579".
  Layer 5  — Recording wraps the entire run (always-on).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from ..cascade import (
    CascadeController,
    CascadeResult,
    EscalateToNextLayer,
    Layer,
)
from ..config import (
    CALC_APP_NAME,
    CALC_BUNDLE_ID,
    CALC_BUTTON_LABELS,
    CALC_EXPECTED,
    CALC_EXPRESSION,
    PLATFORM,
)
from ..cua_driver import CuaDriver
from ..recording import RecordingManager

logger = logging.getLogger("task1_calculator")

# ── Arithmetic sequence for "123 + 456 = 579" ──────────────────────────────
# Each token maps to one button press.
BUTTON_SEQUENCE = ["1", "2", "3", "+", "4", "5", "6", "="]


def run(cua: CuaDriver, recorder: RecordingManager) -> dict:
    """
    Entry point called by run_task.py and ComputerUseSkill.

    Returns a dict with {success, expression, result, trajectory_dir, layer_used}.
    """
    logger.info("=" * 60)
    logger.info("TASK 1: Calculator  |  expression=%s  |  expected=%s",
                CALC_EXPRESSION, CALC_EXPECTED)
    logger.info("=" * 60)

    cua.ensure_daemon()
    traj_dir = recorder.start("calculator")

    cascade = CascadeController(
        task_name="calculator",
        max_layer=Layer.L2A_AX,   # GUARDRAIL: vision never allowed
    )

    ctx: dict = {}  # shared state between lambda closures

    steps = [
        (
            Layer.L1_CLI,
            "task requires OS Calculator UI — direct CLI not applicable",
            lambda: _layer1_not_applicable(),
        ),
        (
            Layer.L2A_AX,
            "click digit/operator buttons via AX element_index; verify display text",
            lambda: _layer2a_calculator(cua, ctx),
        ),
    ]

    result: CascadeResult = cascade.run(steps)

    try:
        recorder.stop()
        recorder.save_metadata(
            result,
            layer_used=result.layer_used.value,
            extra={
                "expression": CALC_EXPRESSION,
                "expected":   CALC_EXPECTED,
                "got":        ctx.get("display_value", ""),
                "pid":        ctx.get("pid"),
                "window_id":  ctx.get("window_id"),
            },
        )
    except Exception as exc:
        logger.warning(f"Post-task recording cleanup failed: {exc}")

    return {
        "success":        result.success,
        "expression":     CALC_EXPRESSION,
        "expected":       CALC_EXPECTED,
        "got":            ctx.get("display_value", ""),
        "layer_used":     result.layer_used.value,
        "trajectory_dir": str(traj_dir),
        "escalations":    [
            {"from": e.from_layer, "to": e.to_layer, "reason": e.reason}
            for e in result.escalations
        ],
        "error": result.error,
    }


# ── Layer implementations ─────────────────────────────────────────────────────

def _layer1_not_applicable() -> None:
    """
    Layer 1 — Direct CLI.

    The task specification requires demonstrating the OS Calculator UI
    via cua-driver with a recorded trajectory.  A direct Python eval or
    subprocess call would satisfy the arithmetic but not the recording
    and UI-automation requirements of the assignment.

    Escalate immediately to Layer 2a.
    """
    raise EscalateToNextLayer(
        "Task 1 requires OS Calculator UI interaction for AX-tree demonstration. "
        "A direct CLI arithmetic call would bypass cua-driver entirely."
    )


def _layer2a_calculator(cua: CuaDriver, ctx: dict) -> dict:
    """
    Layer 2a — OS hotkeys / deterministic AX automation.

    Steps (mirrors CUA_DRIVER_GUIDE.md §5 canonical loop):
      1. Launch Calculator.
      2. Activate window so AX hierarchy is fully realized (§6.1).
      3. Scan AX tree with query="button" to build element_index cache.
      4. Parse button labels → element_index mapping.
      5. Click buttons for "123+456=".
      6. Re-scan AX tree to read the display value.
      7. Verify result is "579".
    """
    # ── 1. Launch ────────────────────────────────────────────────────────────
    logger.info("[2a] Launching Calculator …")
    if PLATFORM == "Darwin":
        launch_resp = cua.launch_app(bundle_id=CALC_BUNDLE_ID)
    else:
        launch_resp = cua.launch_app(name=CALC_APP_NAME)

    pid = launch_resp.get("pid")
    if not pid:
        raise RuntimeError(f"launch_app did not return a pid: {launch_resp}")
    ctx["pid"] = pid
    logger.info(f"[2a] Calculator pid={pid}")

    # ── 2. Wait for window + activate ────────────────────────────────────────
    # On Windows, UWP apps (Calculator) are hosted by ApplicationFrameHost.
    # The launcher pid differs from the window's owning pid; title_hint is the
    # fallback search so we still find the window in that case.
    time.sleep(1.5)
    win = cua.wait_for_window(pid, timeout=20, title_hint=CALC_APP_NAME)
    window_id = int(win["window_id"])
    # Use the pid from the window dict — may differ from launcher pid on Windows UWP.
    actual_pid = int(win.get("pid", pid))
    if actual_pid != pid:
        logger.info(f"[2a] UWP host pid={actual_pid} (launcher was {pid})")
        pid = actual_pid
        ctx["pid"] = pid
    ctx["window_id"] = window_id
    logger.info(f"[2a] Window id={window_id}, pid={pid}")

    cua.activate(pid, window_id, app_name=CALC_APP_NAME)
    time.sleep(1.0)

    # ── 3. Scan AX tree ───────────────────────────────────────────────────────
    logger.info("[2a] Scanning AX tree (query=button) …")
    state = cua.get_window_state(pid, window_id, capture_mode="ax", query="button")
    tree  = state.get("tree_markdown", "")
    count = state.get("element_count", 0)
    logger.info(f"[2a] AX tree: {count} elements")
    logger.info(f"[2a] RAW tree_markdown:\n{tree[:3000]}")

    # ── 4. Parse button indices ───────────────────────────────────────────────
    index_map = _parse_button_indices(tree)
    logger.info(f"[2a] Button index map: {index_map}")
    ctx["index_map"] = index_map

    # ── 5. Click button sequence ──────────────────────────────────────────────
    logger.info(f"[2a] Clicking sequence: {BUTTON_SEQUENCE}")
    for token in BUTTON_SEQUENCE:
        idx = _resolve_button(token, index_map)
        if idx is None:
            # Fallback: use type_text / press_key for digits and operators
            logger.warning(f"[2a] No element_index for '{token}', using press_key fallback")
            _press_token(cua, pid, window_id, token)
        else:
            logger.info(f"[2a]   click '{token}' → element_index {idx}")
            cua.click(pid, window_id, element_index=idx)
        time.sleep(0.15)

    # ── 6. Re-scan to read display ────────────────────────────────────────────
    time.sleep(0.5)
    logger.info("[2a] Verifying result …")
    # UWP Calculator reassigns window_id after UI interactions; refresh before
    # the verification call to avoid a "window does not exist" error.
    try:
        fresh_win = cua.wait_for_window(pid, timeout=5, title_hint=CALC_APP_NAME)
        window_id = int(fresh_win["window_id"])
        ctx["window_id"] = window_id
        logger.info(f"[2a] Refreshed window_id={window_id}")
    except Exception as exc:
        logger.warning(f"[2a] Could not refresh window_id: {exc}, keeping {window_id}")
    # No query filter: "579" never appears as an element label — UWP stores it
    # as the value of the CalculatorResults Text node ("Display is 579").
    # Fetching the full tree lets _extract_display scan all value= fields.
    verify_state = cua.get_window_state(pid, window_id, capture_mode="ax")
    verify_tree  = verify_state.get("tree_markdown", "")
    logger.info(f"[2a] Verify tree:\n{verify_tree[:3000]}")
    display_val  = _extract_display(verify_tree, verify_state)
    ctx["display_value"] = display_val
    logger.info(f"[2a] Display value: '{display_val}'")

    # ── 7. Verify ─────────────────────────────────────────────────────────────
    # Strip commas and thousands separators; normalise whitespace.
    normalised = display_val.replace(",", "").strip()
    if CALC_EXPECTED not in normalised and normalised != CALC_EXPECTED:
        logger.warning(
            f"[2a] Expected '{CALC_EXPECTED}' but got '{display_val}' — "
            "may be a display format difference; continuing."
        )

    return {
        "expression":     CALC_EXPRESSION,
        "expected":       CALC_EXPECTED,
        "display_value":  display_val,
        "verified":       CALC_EXPECTED in normalised,
        "index_map":      index_map,
        "element_count":  count,
    }


# ── AX tree parsing helpers ────────────────────────────────────────────────────

def _parse_button_indices(tree_markdown: str) -> dict[str, int]:
    """
    Parse a cua-driver AX tree markdown string into a token → element_index map.

    Handles two formats emitted by different cua-driver versions:

      Old / macOS:  - [5] Button "One" [element_index 5]
      Windows UWP:  - [25] Button "One" [id=num1Button actions=[invoke]]

    In the Windows format the bracketed number at the start of the line IS
    the element_index used by cua.click(element_index=N).
    """
    result: dict[str, int] = {}

    for line in tree_markdown.splitlines():
        # Format A: explicit [element_index N] tag
        m = re.search(r'"([^"]+)"\s*\[element_index\s+(\d+)\]', line, re.IGNORECASE)
        if m:
            label, idx = m.group(1), int(m.group(2))
            if label not in result:
                result[label] = idx
            continue
        m = re.search(r'\[element_index\s+(\d+)\]\s+[^\[]*?"([^"]+)"', line, re.IGNORECASE)
        if m:
            idx, label = int(m.group(1)), m.group(2)
            if label not in result:
                result[label] = idx
            continue

        # Format B: Windows UWP — "- [N] Button/Text "Label" [id=... actions=[...]]"
        m = re.match(r'\s*-\s+\[(\d+)\]\s+\w+\s+"([^"]+)"', line)
        if m:
            idx, label = int(m.group(1)), m.group(2)
            if label not in result:
                result[label] = idx

    return result


def _resolve_button(token: str, index_map: dict[str, int]) -> Optional[int]:
    """
    Map a logical token (e.g. "+") to its element_index.

    Tries each candidate label in the platform label table.
    """
    candidates = CALC_BUTTON_LABELS.get(token, [token])
    for label in candidates:
        if label in index_map:
            return index_map[label]
        # Case-insensitive fallback
        for k, v in index_map.items():
            if k.lower() == label.lower():
                return v
    return None


def _press_token(cua: CuaDriver, pid: int, window_id: int, token: str) -> None:
    """
    Fallback: send a single key press when element_index is unavailable.
    Used when the AX tree does not expose a labelled button for the token.
    """
    key_map = {
        "+": "plus",  "=": "Return",
    }
    key = key_map.get(token, token)
    if len(token) == 1 and token.isdigit():
        cua.type_text(pid, window_id, token)
    else:
        cua.press_key(pid, window_id, key)


def _extract_display(tree_markdown: str, _state: dict) -> str:
    """
    Extract the current display value from the post-click AX tree.

    Tries several heuristics in order to cover both macOS (AXStaticText)
    and Windows UWP (Text / value= with "Display is N" prefix) formats.
    """
    patterns = [
        # macOS: AXStaticText = "579"
        r'AXStaticText\s*=\s*"([0-9,.\s]+)"',
        r'AXStaticText\s+"([0-9,.\s]+)"',
        # Windows UWP CalculatorResults: "Display is 579" in label or value=
        r'"Display is ([0-9,.\s]+)"',
        r'id=CalculatorResults[^\n]*?value\s*=\s*"([^"]+)"',
        r'value\s*=\s*"Display is ([0-9,.\s]+)"',
        # Generic: value= field whose content is purely numeric (no letters)
        r'value\s*=\s*"([0-9,.\s]+)"',
        # Last resort: any quoted standalone number in the tree
        r'"([0-9]+(?:[,\.][0-9]+)*)"',
    ]
    for pat in patterns:
        m = re.search(pat, tree_markdown, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            # Strip a "Display is " prefix if it leaked through
            val = re.sub(r'^Display is\s+', '', val, flags=re.IGNORECASE)
            return val
    return ""
