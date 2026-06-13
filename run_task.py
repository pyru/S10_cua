#!/usr/bin/env python3
"""
Task runner for the Session 9 computer-use skill.

Usage:
    python run_task.py calculator
    python run_task.py vscode
    python run_task.py vision_canvas

Each task is independently runnable and self-contained.
Trajectories are saved under trajectories/<task>/<timestamp>/.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Make the project root importable regardless of where the script is run from.
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

TASKS = {
    "calculator":   "Task 1 — Calculator arithmetic (Layer 2a, zero vision)",
    "vscode":       "Task 2 — VS Code Electron page tool (Layer 2b, CDP)",
    "vision_canvas":"Task 3 — Browser canvas vision task (Layer 3, V9 vision)",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single Session 9 computer-use task.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {k:16s} — {v}" for k, v in TASKS.items()),
    )
    parser.add_argument(
        "task",
        choices=list(TASKS.keys()),
        help="Which task to run.",
    )
    parser.add_argument(
        "--trajectories",
        default="trajectories",
        help="Base directory for trajectory output (default: trajectories/).",
    )
    parser.add_argument(
        "--gateway-url",
        default=None,
        help="Override LLM Gateway V9 URL (default: http://localhost:8109).",
    )
    args = parser.parse_args()

    print(f"\n{'═' * 60}")
    print(f"  CUA Session 9 — {TASKS[args.task]}")
    print(f"{'═' * 60}\n")

    from skills.computer_use_session9.skill import ComputerUseSkill

    skill = ComputerUseSkill(
        trajectories_dir=args.trajectories,
        gateway_url=args.gateway_url,
    )

    dispatch = {
        "calculator":    skill.run_calculator,
        "vscode":        skill.run_vscode,
        "vision_canvas": skill.run_canvas,
    }

    try:
        result = dispatch[args.task]()
    except Exception as exc:
        logging.error(f"Task failed with unhandled exception: {exc}", exc_info=True)
        sys.exit(1)

    print(f"\n{'─' * 60}")
    print("RESULT:")
    print(json.dumps(result, indent=2, default=str))
    print(f"{'─' * 60}")

    if result.get("trajectory_dir"):
        print(f"\n📁 Trajectory: {result['trajectory_dir']}")
    print(f"✓ Layer used : {result.get('layer_used', '?')}")
    print(f"{'✓' if result.get('success') else '✗'} Success    : {result.get('success')}")

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
