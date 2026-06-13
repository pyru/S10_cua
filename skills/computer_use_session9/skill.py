"""
Session 9 catalogue skill: computer_use.

Drop-in compatible with the Session 9 skill registry (skills.py /
agent_config.yaml).  Register as `computer_use` in the catalogue.

This skill exposes three tasks that collectively demonstrate:
  - All five cascade layers in code
  - cua-driver as the substrate
  - V9 gateway for all LLM and vision calls
  - start_recording / trajectory evidence for every run
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import GATEWAY_URL, TRAJECTORIES_DIR
from .cua_driver import CuaDriver
from .gateway_client import GatewayClient
from .recording import RecordingManager
from .tasks.task1_calculator import run as run_calculator
from .tasks.task2_vscode import run as run_vscode
from .tasks.task3_canvas import run as run_canvas

logger = logging.getLogger("computer_use_session9")


class ComputerUseSkill:
    """
    Session 9 catalogue-compatible computer-use skill.

    Three tasks, five cascade layers, cua-driver substrate, V9 gateway.

    Catalogue registration:
        from skills.computer_use_session9.skill import ComputerUseSkill
        skill = ComputerUseSkill()
        result = skill.run_calculator()
    """

    name = "computer_use"
    description = (
        "Drives the primary OS via cua-driver using a five-layer cascade. "
        "Task 1: Calculator arithmetic (Layer 2a, zero vision). "
        "Task 2: VS Code Electron page tool (Layer 2b, CDP). "
        "Task 3: Browser canvas vision task (Layer 3, multimodal LLM). "
        "All runs recorded; trajectories saved under trajectories/."
    )

    def __init__(
        self,
        trajectories_dir: str = None,
        gateway_url: str = None,
        cua_binary: str = None,
    ):
        from .config import CUA_DRIVER_PATH
        self.cua = CuaDriver(binary=cua_binary or CUA_DRIVER_PATH)
        self.gateway = GatewayClient(gateway_url or GATEWAY_URL)
        self.recorder = RecordingManager(
            self.cua,
            base_dir=trajectories_dir or str(TRAJECTORIES_DIR),
        )

    # ── Task runners ──────────────────────────────────────────────────────────

    def run_calculator(self) -> dict:
        """
        Task 1: drive Windows/macOS Calculator to compute 123 + 456 = 579.
        Uses Layer 2a (AX tree + element_index).  Zero vision calls.
        """
        return run_calculator(self.cua, self.recorder)

    def run_vscode(self) -> dict:
        """
        Task 2: open VS Code with electron_debugging_port and use the
        page tool (CDP) to write a note file.  Layer 2b.
        """
        return run_vscode(self.cua, self.recorder)

    def run_canvas(self) -> dict:
        """
        Task 3: open a browser canvas app, screenshot it, locate a red
        circle via V9 /v1/vision, click it, verify.  Layer 3.
        """
        return run_canvas(self.cua, self.gateway, self.recorder)

    def run_all(self) -> list[dict]:
        """Run all three tasks in sequence.  Returns list of result dicts."""
        results = []
        for name, fn in [
            ("calculator", self.run_calculator),
            ("vscode",     self.run_vscode),
            ("canvas",     self.run_canvas),
        ]:
            logger.info(f"\n{'=' * 60}\nRunning task: {name}\n{'=' * 60}")
            try:
                result = fn()
                results.append(result)
                status = "✓ success" if result.get("success") else "✗ failed"
                logger.info(f"Task {name}: {status}")
            except Exception as exc:
                logger.error(f"Task {name} raised: {exc}", exc_info=True)
                results.append({"task": name, "success": False, "error": str(exc)})
        return results
