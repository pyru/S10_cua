#!/usr/bin/env python3
"""
Run all three Session 9 computer-use tasks in sequence.

Usage:
    python run_all.py
    python run_all.py --trajectories /path/to/traj
    python run_all.py --gateway-url http://localhost:8109

Tasks run in order:
  1. Calculator  (Layer 2a — zero vision)
  2. VS Code     (Layer 2b — Electron page tool)
  3. Canvas      (Layer 3  — vision)

Each task records a trajectory under trajectories/<task>/<timestamp>/.
A combined summary is printed at the end.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all Session 9 CUA tasks.")
    parser.add_argument("--trajectories", default="trajectories")
    parser.add_argument("--gateway-url",  default=None)
    parser.add_argument(
        "--skip",
        nargs="*",
        choices=["calculator", "vscode", "vision_canvas"],
        default=[],
        help="Skip one or more tasks.",
    )
    args = parser.parse_args()

    from skills.computer_use_session9.skill import ComputerUseSkill

    skill = ComputerUseSkill(
        trajectories_dir=args.trajectories,
        gateway_url=args.gateway_url,
    )

    plan = [
        ("calculator",    "Task 1 — Calculator (Layer 2a)",       skill.run_calculator),
        ("vscode",        "Task 2 — VS Code    (Layer 2b)",       skill.run_vscode),
        ("vision_canvas", "Task 3 — Canvas     (Layer 3)",        skill.run_canvas),
    ]

    results = []
    overall_start = time.time()

    for task_key, label, fn in plan:
        if task_key in (args.skip or []):
            print(f"\n⏭  Skipping: {label}")
            continue

        print(f"\n{'═' * 60}")
        print(f"  {label}")
        print(f"{'═' * 60}")

        t0 = time.time()
        try:
            result = fn()
            elapsed = time.time() - t0
            result["task"] = task_key
            result["elapsed_s"] = round(elapsed, 2)
            results.append(result)
            status = "✓" if result.get("success") else "✗"
            print(f"\n{status} {label}  [{elapsed:.1f}s]  layer={result.get('layer_used', '?')}")
        except Exception as exc:
            elapsed = time.time() - t0
            logging.error(f"Task {task_key} raised: {exc}", exc_info=True)
            results.append({
                "task": task_key, "success": False,
                "error": str(exc), "elapsed_s": round(elapsed, 2),
            })
            print(f"\n✗ {label}  [{elapsed:.1f}s]  EXCEPTION: {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    overall = time.time() - overall_start
    print(f"\n{'═' * 60}")
    print(f"  SUMMARY  (total {overall:.1f}s)")
    print(f"{'═' * 60}")
    for r in results:
        icon  = "✓" if r.get("success") else "✗"
        layer = r.get("layer_used", "?")
        tdir  = r.get("trajectory_dir", "—")
        escs  = len(r.get("escalations", []))
        print(
            f"  {icon} {r['task']:16s}  "
            f"layer={layer}  escalations={escs}  "
            f"elapsed={r.get('elapsed_s','?')}s"
        )
        if tdir and tdir != "—":
            print(f"      📁 {tdir}")

    n_ok  = sum(1 for r in results if r.get("success"))
    n_tot = len(results)
    print(f"\nPassed: {n_ok}/{n_tot}")

    # Write combined result JSON
    summary_path = Path(args.trajectories) / "run_all_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {"results": results, "total_elapsed_s": round(overall, 2)},
            indent=2,
            default=str,
        )
    )
    print(f"📄 Summary: {summary_path}")

    sys.exit(0 if n_ok == n_tot else 1)


if __name__ == "__main__":
    main()
