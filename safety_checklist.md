# Safety Checklist — CUA Session 9 Computer-Use Skill

Following CUA_DRIVER_GUIDE.md §13 guidance before any run touching important data.

---

## Pre-Run Checklist

### Data Safety
- [ ] **Close sensitive applications** before running. The agent clicks based on AX
      element_index — a mis-indexed click in a wrong window could activate
      unintended controls in any visible app.
- [ ] **Save all open work** before starting any task. The agent synthesises
      keyboard events via `CGEventPostToPid` / `SendInput` which reach any app.
- [ ] **Use a dedicated test workspace for Task 2 (VS Code)**. The note file
      `cua_session9_note.md` is written to the current working directory. Verify
      this is not inside a git repository with auto-commit hooks or cloud sync.
- [ ] **For Task 3 (Canvas)**: confirm the browser's default download location
      is not set to a sensitive directory. The canvas task only clicks; it does
      not trigger downloads.

### System State
- [ ] **Calculator is closed** before Task 1 (the task opens it fresh).
- [ ] **VS Code workspace is empty or a dedicated test workspace** for Task 2.
- [ ] **Default browser is a test profile** or the canvas URL is safe to open.
- [ ] **cua-driver daemon is not already recording** another session:
      ```
      cua-driver call get_recording_state '{}'
      ```

### Permissions
- [ ] (macOS) `cua-driver permissions grant` has been run.
      Both Accessibility AND Screen Recording are granted to `com.trycua.driver`.
- [ ] (Windows) No extra permission steps required for Calculator/UWP apps.
- [ ] (Linux) Running under X11; `QT_ACCESSIBILITY=1` set if targeting Qt apps.

### Gateway
- [ ] LLM Gateway V9 is running:
      ```
      curl http://localhost:8109/v1/status
      ```
- [ ] At least one vision-capable provider is configured in `llm_gatewayV9/.env`
      (needed only for Task 3). Gemini and Groq support vision.

---

## During Run

- The agent cursor overlay shows where cua-driver is acting. **Watch it.**
- If the agent appears to be clicking in the wrong window, kill the daemon:
  ```
  # macOS/Linux
  pkill -f "cua-driver serve"
  # Windows
  taskkill /F /IM cua-driver.exe
  ```
- Tasks have no auto-retry loop. If a task fails, check the trajectory directory
  for the recorded tool calls and diagnose before re-running.

---

## Post-Run Checklist

- [ ] Review `trajectories/<task>/<timestamp>/run_metadata.json`
      for the layer used and any escalations.
- [ ] Close Calculator if Task 1 left it open (expected — task doesn't close it).
- [ ] Close VS Code if Task 2 left it open.
- [ ] Close the browser if Task 3 left it open.
- [ ] Verify `cua-driver call get_recording_state '{}'` shows recording stopped.

---

## Failure Modes and Mitigations

| Failure | Symptom | Mitigation |
|---|---|---|
| Empty AX tree | `element_count: 0` | (macOS) Grant permissions, activate app first. (Win) App may block UIA. |
| Daemon not running | "Element index N not found in cache" | `cua-driver serve &` then retry. |
| VS Code not found | `launch_app` error | Set `VSCODE_APP_NAME` env var to the correct binary name. |
| Vision LLM refused | 4xx from /v1/vision | Check gateway has a vision-capable provider; try `provider=gemini`. |
| Calculator display format | `579` vs `5.79E2` | Check CALC_EXPECTED in config.py matches your OS Calculator's display format. |
| Canvas browser not found | Browser window not detected | Set `CANVAS_BROWSER` env var; or open canvas HTML manually and note the pid. |
| Screenshot not written | `/tmp/*.png` missing | (macOS) Grant Screen Recording to cua-driver. Check `screenshot_out_file` path is writable. |
| TaskLayerBlocked | Task tried to escalate past max_layer | Expected for Task 1 — no vision is a hard constraint, not a bug. |

---

## Scope Limitations

- This skill operates on the **host OS** (not a VM/sandbox). It shares the user's
  credentials, open files, and clipboard.
- Task 1 and Task 2 make **no LLM calls** (pure deterministic automation).
  Only Task 3 sends data to the V9 gateway.
- Screenshots taken in Task 3 are stored in the system temp directory and the
  trajectory folder. Review before committing to a public repository.
- The `page` tool in Task 2 runs JavaScript in VS Code's renderer process.
  Evaluate any `script=` argument before running in a sensitive environment.
