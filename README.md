Session 10
# CUA Session 10
 — Computer-Use Skill

A production-ready computer-use skill for the **Session 10
 catalogue** built on
[cua-driver](https://github.com/trycua/cua) and the **LLM Gateway V9** (port 8109).

Three tasks demonstrate all five cascade layers.  Every run is recorded via
`start_recording` and saved as a trajectory directory under `trajectories/`.

https://www.youtube.com/watch?v=0dRCjfMh4Sw
---

## Quick Start

```bash
# 0. Install cua-driver (one-time)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"
cua-driver permissions grant   # macOS only
cua-driver serve &             # start daemon

# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Start LLM Gateway V9 (needed for Task 3 only)
cd llm_gatewayV9 && ./run.sh   # starts on port 8109

# 3. Run individual tasks
python run_task.py calculator
python run_task.py vscode
python run_task.py vision_canvas

# 4. Run all tasks
python run_all.py
```

---

## Five-Layer Architecture

The cascade controller (`skills/computer_use_session9/cascade.py`) enforces a
strict escalation order.  Each layer is attempted in sequence; a layer may only
escalate when it explicitly raises `EscalateToNextLayer`.  A hard ceiling
(`max_layer`) prevents any task from silently falling through to a more
expensive path.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1 ── Direct deterministic API / CLI                          │
│             No UI automation.  Subprocess, SDK, file I/O.           │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2a ── OS hotkeys / AX tree + element_index                   │
│              cua-driver scans the accessibility tree and clicks      │
│              elements by index.  Deterministic.  Zero LLM calls.    │
│              Zero vision calls.                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2b ── Electron page tool (CDP) / cheap text LLM              │
│              For Electron/browser apps launched with                 │
│              electron_debugging_port.  DOM selectors, JS evaluation. │
│              No screenshots.                                         │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3 ── Vision-based perception                                  │
│             Screenshot → V9 /v1/vision (multimodal LLM) →           │
│             pixel coordinates → pixel-addressed click.               │
│             Only when AX and DOM are both opaque.                    │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4 ── Human confirmation / safe stop                           │
│             Automation exhausted.  Surface description to operator.  │
│             (Not hit in any of the three included tasks.)            │
└─────────────────────────────────────────────────────────────────────┘
  Layer 5 ── Memory / logging / replay / trajectory evidence
             Always-on.  Wraps every run with start_recording.
             Saved under trajectories/<task>/<YYYYMMDD_HHMMSS>/.
```

### Cascade Decision Logic

```python
# From cascade.py — the controller that makes the discipline visible
cascade = CascadeController(task_name="calculator", max_layer=Layer.L2A_AX)
result = cascade.run([
    (Layer.L1_CLI,  "check for direct CLI",        fn_layer1),  # escalates
    (Layer.L2A_AX,  "drive AX tree with hotkeys",  fn_layer2a), # succeeds
])
# If fn_layer2a tried to escalate further → LayerBlocked (guardrail fires)
```

Every escalation is logged:
```
[CASCADE] Layer 1 → escalating to Layer 2a:
          Task 1 requires OS Calculator UI — direct CLI not applicable.
[CASCADE] Layer 2a ✓ SUCCESS  (1.84s)
```

---

## The Three Tasks

### Task 1 — Calculator (Layer 2a, ZERO vision)

**File:** `skills/computer_use_session9/tasks/task1_calculator.py`

**Expression:** `123 + 456 = 579`

**Why Layer 2a?**  The OS Calculator (Windows UWP / macOS) exposes a full
UIA/AX tree with every button labelled.  `get_window_state(capture_mode="ax")`
returns all button `element_index` values.  The agent clicks them in sequence
and reads the display element from the post-click tree.

**Guardrail:**  `max_layer=Layer.L2A_AX`.  If any code path tried to call
`/v1/vision`, `LayerBlocked` would be raised before the HTTP call reached
the gateway.  Verified by `grep -n "vision" task1_calculator.py` → no calls.

**Cascade path:**
1. Layer 1 escalates: "task requires OS Calculator UI demonstration"
2. Layer 2a succeeds: launch → activate → scan → click → verify

---

### Task 2 — VS Code Electron (Layer 2b)

**File:** `skills/computer_use_session9/tasks/task2_vscode.py`

**What it does:** Launches VS Code with `electron_debugging_port=9222`, uses
the `page` tool (Chrome DevTools Protocol) to evaluate JavaScript in the
renderer process and write a note file, then verifies via the DOM.

**Why Layer 2b?**  VS Code renders its entire UI as HTML inside Chromium.
The AX tree shows only the outer window frame and an opaque `AXWebArea` —
no actionable buttons, no text fields.  The `page` tool is the only path
that can reach VS Code's internal state without screenshots.

**Electron page path (CUA_DRIVER_GUIDE.md §7.2):**
```bash
cua-driver call launch_app '{"name":"code","electron_debugging_port":9222}'
cua-driver call page '{"pid":1234,"action":"evaluate","script":"document.title"}'
```

**Cascade path:**
1. Layer 1 escalates: "task requires Electron page path demonstration"
2. Layer 2b succeeds: launch with debug port → page evaluate → write file

---

### Task 3 — Canvas Vision (Layer 3)

**File:** `skills/computer_use_session9/tasks/task3_canvas.py`
**Canvas:** `assets/canvas_target.html`

**What it does:** Opens a browser-based canvas application with a randomly-
positioned red circle drawn via Canvas 2D API.  Uses the V9 gateway's
`/v1/vision` endpoint to locate the circle from a screenshot and click it.

**Why Layer 3?**  HTML `<canvas>` content is pixel-rendered — the target's
position does not appear in any DOM element, AX node, or CDP selector
(CUA_DRIVER_GUIDE.md §7.3: "HTML canvas and WebGL content").

**Full cascade (all three automated layers attempted):**
1. Layer 1 escalates: "no CLI path for canvas interaction"
2. Layer 2a: scans browser AX tree → canvas is opaque → escalates
3. Layer 2b: reads DOM status text → canvas coords found but screen mapping
   unreliable without stable bounding rect → escalates
4. Layer 3 succeeds:
   ```python
   # Screenshot
   cua.get_window_state(pid, wid, capture_mode="vision",
                        screenshot_out_file="/tmp/screenshot.png")
   # Locate target
   coords = gateway.vision_structured("/tmp/screenshot.png",
       "Find the red circle. Return {x, y} pixel coordinates.",
       schema={"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}}})
   # Click
   cua.click(pid, wid, x=coords["x"], y=coords["y"])
   # Verify
   verdict = gateway.vision_structured("/tmp/verify.png",
       "Did the agent hit the red target? Return {hit: bool, status_text: str}")
   ```

---

## Recording and Trajectory Evidence

All runs use `start_recording` / `stop_recording` (Layer 5).

```
trajectories/
├── calculator/
│   └── 20250613_143022/
│       ├── turn_0001.json   ← ensure_daemon
│       ├── turn_0002.json   ← launch_app (Calculator)
│       ├── turn_0003.json   ← bring_to_front
│       ├── turn_0004.json   ← get_window_state (AX scan, query=button)
│       ├── turn_0005.json   ← click (element_index → "1")
│       ├── ...
│       ├── turn_0013.json   ← get_window_state (verify "579")
│       └── run_metadata.json
├── vscode/
│   └── 20250613_143208/
│       ├── turn_0001.json   ← launch_app (electron_debugging_port=9222)
│       ├── turn_0002.json   ← page evaluate (write file)
│       ├── turn_0003.json   ← page evaluate (document.title)
│       └── run_metadata.json
└── vision_canvas/
    └── 20250613_143441/
        ├── turn_0001.json   ← OS open (browser)
        ├── turn_0002.json   ← get_window_state (AX — escalates)
        ├── turn_0003.json   ← page evaluate (DOM — escalates)
        ├── turn_0004.json   ← get_window_state (vision, screenshot)
        ├── turn_0005.json   ← click (x=412, y=287)
        ├── turn_0006.json   ← get_window_state (verify screenshot)
        └── run_metadata.json
```

**Replay any trajectory:**
```bash
cua-driver replay_trajectory trajectories/calculator/20250613_143022/
```

**run_metadata.json schema:**
```json
{
  "task": "calculator",
  "timestamp": "2025-06-13T14:30:22.411",
  "elapsed_s": 4.812,
  "layer_used": "2a",
  "success": true,
  "escalations": [
    {"from": "1", "to": "2a", "reason": "task requires OS Calculator UI..."}
  ],
  "error": ""
}
```

---

## Setup Details

### cua-driver

```bash
# Install (macOS/Linux)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"

# Windows — download from GitHub Releases and add to PATH

# Grant permissions (macOS only)
~/.local/bin/cua-driver permissions grant

# Start daemon (required for element_index operations)
cua-driver serve &
cua-driver status    # verify

# Inspect tools
cua-driver list-tools
cua-driver describe get_window_state
```

**Override binary path:**
```bash
export CUA_DRIVER_PATH=/path/to/cua-driver
```

### LLM Gateway V9

Only required for Task 3 (vision).

```bash
cd llm_gatewayV9
./run.sh    # starts on port 8109

# Verify
curl http://localhost:8109/v1/status
```

**Override gateway URL:**
```bash
export LLM_GATEWAY_V9_URL=http://localhost:8109
```

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `CUA_DRIVER_PATH` | auto-detected | Path to cua-driver binary |
| `LLM_GATEWAY_V9_URL` | `http://localhost:8109` | V9 gateway URL |
| `TRAJECTORIES_DIR` | `trajectories` | Base dir for run recordings |
| `CALC_APP_NAME` | `Calculator` | App name for Task 1 |
| `VSCODE_APP_NAME` | `code` | VS Code executable for Task 2 |
| `VSCODE_DEBUGGING_PORT` | `9222` | Electron debugging port |
| `CANVAS_BROWSER` | `msedge` (Win) / `Google Chrome` (mac) | Browser for Task 3 |

---

## Failure Modes and Mitigations

| Failure | Root Cause | Fix |
|---|---|---|
| `CuaError: Empty AX tree` | Missing accessibility permission, app not activated | macOS: `cua-driver permissions grant`, re-activate app. Windows: check UIA support for the app. |
| `Element index N not found in cache` | Daemon not running; element-index cache is process-scoped (§3.2) | `cua-driver serve &` before running. |
| VS Code page tool timeout | VS Code not launched with debugging port, or startup delay | Set `VSCODE_APP_NAME` correctly; increase sleep in task2_vscode.py. |
| Vision returns wrong coordinates | Screenshot resolution mismatch, model confusion | Check screenshot path exists; try `provider=gemini` in gateway_client. |
| `LayerBlocked` in Task 1 | Attempted to escalate past `max_layer=L2A_AX` | Expected behaviour — guardrail working correctly. |
| No browser window detected | Browser process not trackable by pid | Open canvas HTML manually, note the pid, pass via env or modify config. |
| `Gateway not reachable` | V9 not running | `cd llm_gatewayV9 && ./run.sh` |

---

## Project Structure

```
Session10Code/
├── CUA_DRIVER_GUIDE.md          ← read before implementation
├── skills/
│   └── computer_use_session9/   ← Session 10
 catalogue drop-in
│       ├── __init__.py
│       ├── skill.py             ← ComputerUseSkill class
│       ├── config.py            ← all tunable parameters
│       ├── cua_driver.py        ← CuaDriver adapter (34 tools)
│       ├── cascade.py           ← five-layer cascade controller
│       ├── gateway_client.py    ← V9 gateway (chat + /v1/vision)
│       ├── recording.py         ← Layer 5: start/stop_recording
│       └── tasks/
│           ├── task1_calculator.py   ← Layer 2a
│           ├── task2_vscode.py       ← Layer 2b (Electron)
│           └── task3_canvas.py       ← Layer 3 (vision)
├── assets/
│   └── canvas_target.html       ← Task 3 canvas app (no ARIA in canvas)
├── trajectories/
│   ├── calculator/              ← Task 1 evidence
│   ├── vscode/                  ← Task 2 evidence
│   └── vision_canvas/           ← Task 3 evidence
├── run_task.py                  ← python run_task.py calculator|vscode|vision_canvas
├── run_all.py                   ← python run_all.py
├── requirements.txt
├── .gitignore
├── README.md
├── demo_script.md               ← YouTube / live demo guide
└── safety_checklist.md          ← pre/post-run checklist
```

---

## Safety Notes

See `safety_checklist.md` for the full pre-run checklist.

Key points:
- Save all open work before running. The agent synthesises keyboard events
  that reach any focused window.
- Use a dedicated VS Code workspace for Task 2.
- Screenshots in Task 3 contain whatever is on screen. Review before committing.
- The `page` tool evaluates JavaScript in VS Code's renderer. Review any
  `script=` argument before running in a production environment.
- Kill the daemon (`pkill -f "cua-driver serve"` / `taskkill /F /IM cua-driver.exe`)
  if the agent appears to be acting in the wrong window.
