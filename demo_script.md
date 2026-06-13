# Demo Script — CUA Session 9 Computer-Use Skill
## YouTube / Live Demo Guide

**Agent-cursor overlay must be visible throughout.**  
Set `set_agent_cursor_enabled: true` before the first task (the skill does this automatically).

---

## Setup (before recording)

```bash
# Terminal 1 — LLM Gateway V9
cd llm_gatewayV9
./run.sh        # starts on port 8109

# Terminal 2 — cua-driver daemon
cua-driver serve &
cua-driver status   # confirm: "daemon is running"

# (macOS only) grant permissions once
cua-driver permissions grant
```

---

## Scene 1 — Architecture Overview (60 seconds)

**[SCREEN: code editor open to cascade.py]**

> "The five-layer cascade controller is the architectural centrepiece.
> The discipline rule is that we always attempt the cheapest, most
> deterministic layer first and escalate only when that layer can't
> complete the task.
>
> Layer 1 is direct CLI or API — no UI at all.
> Layer 2a is deterministic AX tree automation — no vision, no LLM.
> Layer 2b is the Electron page tool — CDP JavaScript evaluation.
> Layer 3 is vision — a screenshot plus a multimodal LLM call.
> Layer 4 is the human safety stop — all automation exhausted.
> Layer 5 is always-on recording — it wraps everything.
>
> You can see each layer declared explicitly in cascade.py as a Python
> Enum. The `CascadeController.run()` method will raise `LayerBlocked`
> rather than silently escalating past the declared ceiling — that's
> the guardrail that makes Task 1's zero-vision requirement enforceable
> in code, not just in docs."

---

## Scene 2 — Task 1: Calculator (Layer 2a, zero vision)  ~90 seconds

**[SCREEN: terminal]**

```bash
python run_task.py calculator
```

**[NARRATE as it runs]**

> "Task 1 drives the OS Calculator using only the AX accessibility tree.
> Watch the cascade log: Layer 1 escalates immediately — we could do
> 123+456 in Python, but the task requires the OS UI. Layer 2a takes over.
>
> cua-driver launches Calculator, activates the window so the AX
> hierarchy is realised, then calls get_window_state with capture_mode='ax'
> and query='button'. The response is a Markdown tree of every button
> with its element_index number.
>
> The agent parses that tree, maps '1', '2', '3', '+', '4', '5', '6', '='
> to their element indices, and clicks them in order. No screenshot is
> ever taken. The max_layer guardrail is set to L2A_AX — if the code
> accidentally tried to call /v1/vision, LayerBlocked would fire."

**[SHOW: Calculator window with 579 on display]**

> "The verification step re-scans the AX tree looking for '579' in the
> display text element. Confirmed. The trajectory is now saved under
> trajectories/calculator/."

---

## Scene 3 — Task 2: VS Code (Layer 2b, Electron page tool)  ~120 seconds

**[SCREEN: terminal]**

```bash
python run_task.py vscode
```

**[NARRATE]**

> "Task 2 demonstrates why the Electron page tool exists. VS Code renders
> its entire UI as HTML inside Chromium — the AX tree sees the outer window
> frame and then a single opaque web area. There are no button elements,
> no text field elements at the AX level.
>
> The fix is to launch VS Code with --remote-debugging-port, which cua-driver
> wraps as electron_debugging_port. Now the `page` tool can speak Chrome
> DevTools Protocol directly to the renderer process.
>
> Layer 1 escalates — we could write the note file with Python's open(),
> but that would bypass the Electron demonstration. Layer 2b takes over:
> cua-driver launches VS Code with the debugging port, waits for the window,
> and then uses `page evaluate` to run JavaScript in the renderer process
> to write the note content."

**[SHOW: note file appearing in VS Code editor, trajectory directory]**

---

## Scene 4 — Task 3: Canvas Vision (Layer 3)  ~180 seconds

**[SCREEN: terminal alongside browser window]**

```bash
python run_task.py vision_canvas
```

**[NARRATE as cascade escalates through layers]**

> "Task 3 shows the full cascade in action — three layers attempt and
> escalate before vision is used.
>
> Layer 1: no CLI path. Layer 2a: we scan the browser's AX tree — but
> the canvas element is completely opaque to UIA. The tree shows
> AXWebArea and then nothing useful inside the canvas boundary.
> Layer 2a escalates.
>
> Layer 2b: we try CDP — we can read the status bar text from the DOM,
> and we can even see the canvas-local coordinates of the target in the
> status text. But converting those to window-local pixel coordinates
> that cua-driver can click requires the canvas bounding rect, and the
> offset calculation is fragile across zoom levels. We escalate with a
> clear reason logged.
>
> Layer 3: cua-driver takes a screenshot in vision mode. The PNG is
> encoded as a base64 data URL and POSTed to the V9 gateway's
> /v1/vision endpoint at localhost:8109. The multimodal LLM returns
> JSON with pixel coordinates for the red circle's centre."

**[SHOW: the click landing on the red dot, status bar showing '✓ HIT!']**

> "The agent clicks at the returned coordinates. We take a verification
> screenshot and call /v1/vision again, asking 'did the click hit the
> target?'. The LLM reads the status bar text '✓ HIT!' and confirms success.
>
> All three layers of vision calls — the locate call and the verify call —
> went through the V9 gateway. No external paid API. No third-party
> agentic framework. Just cua-driver and the gateway."

---

## Scene 5 — Trajectory Evidence  ~60 seconds

**[SCREEN: file explorer showing trajectories/ directory tree]**

```bash
# Show the directory structure
ls -R trajectories/

# Replay Task 1 to demonstrate determinism
cua-driver replay_trajectory trajectories/calculator/<timestamp>/
```

> "Each task saved a trajectory directory with every tool call numbered
> in order. The run_metadata.json shows which layer was used, how long
> it took, and the escalation chain. This is Layer 5 — always-on
> recording — and replay_trajectory lets us reproduce any run deterministically."

---

## Wrap-Up (30 seconds)

> "Three tasks, five layers, one cua-driver binary, one V9 gateway.
> The cascade discipline isn't just documented — it's enforced by the
> LayerBlocked guardrail and visible in every log line that says
> 'Layer 2a escalating to Layer 2b: canvas has no AX structure'.
>
> The full source is in skills/computer_use_session9/.  Drop it into the
> Session 9 catalogue by importing ComputerUseSkill from skill.py."

---

## Troubleshooting During Demo

| Issue | Quick Fix |
|---|---|
| Calculator wrong display | Retry; Windows UWP sometimes needs a second activation |
| VS Code page tool timeout | Increase `VSCODE_DEBUGGING_PORT` grace time; VS Code cold start can be 5-8s |
| Vision returns wrong coords | Check V9 gateway has a multimodal provider (Gemini works well) |
| "Empty AX tree" | Run `cua-driver permissions grant`; restart daemon |
| Gateway not reachable | `cd llm_gatewayV9 && ./run.sh` |
