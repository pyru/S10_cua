"""
Recording and trajectory management — Layer 5 (always-on).

Every run is wrapped with start_recording / stop_recording.
Trajectory directories are created at:
  trajectories/<task_name>/<YYYYMMDD_HHMMSS>/

The directory contains:
  - cua-driver's turn-numbered trajectory files (tool call + result per turn)
  - run_metadata.json  (task name, layer_used, elapsed, success, escalations)

The trajectory directory is returned so it can be submitted as evidence
and replayed with `cua-driver replay_trajectory <dir>`.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("recording")


class RecordingManager:
    """
    Wraps each task run with cua-driver's start_recording / stop_recording.
    Saves supplementary metadata as run_metadata.json for audit purposes.

    Usage (in each task):
        recorder.start("calculator")
        try:
            result = ... run task ...
        finally:
            recorder.stop()
            recorder.save_metadata(result, layer_used="2a")
    """

    def __init__(self, cua, base_dir: str = "trajectories"):
        self.cua       = cua
        self.base_dir  = Path(base_dir)
        self._dir:      Optional[Path] = None
        self._task:     Optional[str]  = None
        self._t0:       float          = 0.0
        self._recording = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, task_name: str) -> Path:
        """
        Begin recording.  Creates a timestamped directory and calls
        cua-driver start_recording.  Returns the trajectory Path.

        Layer 5 note: failures in the recording machinery are logged as
        warnings rather than exceptions — the task itself is more important
        than the recording.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        traj_dir  = self.base_dir / task_name / timestamp
        traj_dir.mkdir(parents=True, exist_ok=True)

        self._dir   = traj_dir
        self._task  = task_name
        self._t0    = time.time()
        self._recording = False

        try:
            self.cua.start_recording(str(traj_dir))
            self._recording = True
            logger.info(f"[LAYER 5] Recording started → {traj_dir}")
        except Exception as exc:
            logger.warning(
                f"[LAYER 5] start_recording failed ({exc}); "
                "proceeding without cua-driver trajectory capture"
            )

        # Enable agent-cursor overlay so the demo video shows cursor position.
        try:
            self.cua.set_agent_cursor_enabled(True)
            self.cua.set_agent_cursor_style("ring")
            self.cua.set_agent_cursor_motion("smooth")
        except Exception as exc:
            logger.debug(f"[LAYER 5] agent cursor enable failed (non-fatal): {exc}")

        return traj_dir

    def stop(self) -> Optional[Path]:
        """Stop recording.  Returns the trajectory directory."""
        if self._recording:
            try:
                self.cua.stop_recording()
                logger.info(f"[LAYER 5] Recording stopped → {self._dir}")
            except Exception as exc:
                logger.warning(f"[LAYER 5] stop_recording failed: {exc}")
            self._recording = False
        return self._dir

    def save_metadata(
        self,
        result: Any,
        layer_used: str,
        extra: dict = None,
    ) -> Optional[Path]:
        """
        Write run_metadata.json alongside the cua-driver trajectory files.

        `result` may be a CascadeResult dataclass or any object with
        .success, .output, .escalations, .error attributes, or a plain bool.
        """
        if not self._dir:
            return None

        elapsed = time.time() - self._t0
        meta = {
            "task":        self._task,
            "timestamp":   datetime.now().isoformat(),
            "elapsed_s":   round(elapsed, 3),
            "layer_used":  layer_used,
            "success":     _get_attr(result, "success", bool(result)),
            "output":      _safe_json(_get_attr(result, "output", None)),
            "escalations": _safe_json(_get_attr(result, "escalations", [])),
            "error":       _get_attr(result, "error", ""),
            "trajectory_dir": str(self._dir),
        }
        if extra:
            meta.update(extra)

        path = self._dir / "run_metadata.json"
        path.write_text(json.dumps(meta, indent=2, default=str))
        logger.info(f"[LAYER 5] Metadata written → {path}")
        return path

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def current_dir(self) -> Optional[Path]:
        return self._dir

    @property
    def is_recording(self) -> bool:
        return self._recording


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_attr(obj: Any, attr: str, default: Any) -> Any:
    if hasattr(obj, attr):
        return getattr(obj, attr)
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default


def _safe_json(obj: Any) -> Any:
    """Recursively make an object JSON-serialisable."""
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(i) for i in obj]
    if isinstance(obj, Path):
        return str(obj)
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
