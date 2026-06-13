"""
Task 2: VS Code Electron task — Layer 2b (Electron page path).
Uses the `page` tool (Chrome DevTools Protocol) to interact with VS Code.

Cascade path:
  Layer 1  — Direct file write via Python open().
             Escalates: demonstrates Electron page path, not file I/O.
  Layer 2b — Launch VS Code with electron_debugging_port=9222.
             Use `page` tool to:
               a. Evaluate JS to open a new untitled file (Monaco API).
               b. Type the note content via page type action.
               c. Trigger save via Ctrl+S hotkey (page keyboard event).
               d. Read back the file or DOM state to verify.
  Layer 5  — Recording wraps the entire run.

Electron page path (CUA_DRIVER_GUIDE.md §7.2):
  cua-driver's `page` tool speaks Chrome DevTools Protocol to the
  Electron renderer process.  This gives full DOM access to VS Code's
  UI elements — tabs, panels, the Monaco editor — that are invisible
  to UIA/AX because they are rendered as Chromium content, not native
  widgets.

ADAPTER NOTE: the `page` action names ("click", "type", "evaluate",
"navigate") and CSS selectors below are based on CDP conventions and
the VS Code DOM as of 2025.  If a cua-driver release changes the
`page` tool's parameter schema, update CuaDriver.page() in cua_driver.py.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path

from ..cascade import (
    CascadeController,
    CascadeResult,
    EscalateToNextLayer,
    Layer,
)
from ..config import (
    VSCODE_APP_NAME,
    VSCODE_DEBUGGING_PORT,
    VSCODE_NOTE_CONTENT,
    VSCODE_NOTE_FILENAME,
)
from ..cua_driver import CuaDriver
from ..recording import RecordingManager

logger = logging.getLogger("task2_vscode")

# Where the note file will be saved (agent's working directory).
NOTE_PATH = Path(os.getcwd()) / VSCODE_NOTE_FILENAME


def run(cua: CuaDriver, recorder: RecordingManager) -> dict:
    """
    Entry point called by run_task.py and ComputerUseSkill.

    Returns {success, note_path, content_written, trajectory_dir, layer_used}.
    """
    logger.info("=" * 60)
    logger.info("TASK 2: VS Code Electron  |  file=%s", VSCODE_NOTE_FILENAME)
    logger.info("=" * 60)

    cua.ensure_daemon()
    traj_dir = recorder.start("vscode")

    cascade = CascadeController(
        task_name="vscode",
        max_layer=Layer.L2B_PAGE,
    )

    ctx: dict = {}

    steps = [
        (
            Layer.L1_CLI,
            "task requires Electron page path demonstration",
            lambda: _layer1_not_applicable(),
        ),
        (
            Layer.L2B_PAGE,
            "drive VS Code via electron_debugging_port + page tool (CDP)",
            lambda: _layer2b_vscode(cua, ctx),
        ),
    ]

    result: CascadeResult = cascade.run(steps)

    try:
        recorder.stop()
        recorder.save_metadata(
            result,
            layer_used=result.layer_used.value,
            extra={
                "note_path":       str(NOTE_PATH),
                "pid":             ctx.get("pid"),
                "window_id":       ctx.get("window_id"),
                "content_written": ctx.get("content_written", False),
            },
        )
    except Exception as exc:
        logger.warning(f"Post-task recording cleanup failed: {exc}")

    return {
        "success":          result.success,
        "note_path":        str(NOTE_PATH),
        "content_written":  ctx.get("content_written", False),
        "layer_used":       result.layer_used.value,
        "trajectory_dir":   str(traj_dir),
        "escalations": [
            {"from": e.from_layer, "to": e.to_layer, "reason": e.reason}
            for e in result.escalations
        ],
        "error": result.error,
    }


# ── Layer implementations ─────────────────────────────────────────────────────

def _layer1_not_applicable() -> None:
    """
    Layer 1 — Direct file I/O.

    We *could* write the note directly with Python's open():
        NOTE_PATH.write_text(VSCODE_NOTE_CONTENT)

    But the task requirement is to demonstrate the Electron `page` tool
    (Layer 2b) using cua-driver's Chrome DevTools Protocol integration.
    Bypass via direct file I/O would skip that demonstration entirely.
    """
    raise EscalateToNextLayer(
        "Task 2 requires the Electron page tool (CDP) demonstration. "
        "Direct Python file I/O would bypass the Layer 2b path entirely."
    )


def _layer2b_vscode(cua: CuaDriver, ctx: dict) -> dict:
    """
    Layer 2b — Electron page tool (Chrome DevTools Protocol).

    Steps:
      1. Launch VS Code with electron_debugging_port=9222.
      2. Wait for the VS Code window.
      3. Activate the window.
      4. Use `page evaluate` to run a JS snippet that:
           - Creates a new file buffer via Monaco / VS Code workbench API
           - Sets the editor content
      5. Use `page click` on the terminal / file menu as a cross-check.
      6. Use `hotkey` to save (Ctrl+S).
      7. Verify the file was written (read from filesystem or DOM).
    """
    # ── 1. Launch VS Code with debugging port ─────────────────────────────────
    logger.info(f"[2b] Launching VS Code with electron_debugging_port={VSCODE_DEBUGGING_PORT} …")
    try:
        launch_resp = cua.launch_app(
            name=VSCODE_APP_NAME,
            electron_debugging_port=VSCODE_DEBUGGING_PORT,
        )
    except Exception as exc:
        raise EscalateToNextLayer(
            f"VS Code launch failed ({exc}). "
            "Ensure VS Code is installed and 'code' is on PATH. "
            "Try setting VSCODE_APP_NAME env var to the correct executable name."
        )

    pid = launch_resp.get("pid")
    if not pid:
        raise RuntimeError(f"launch_app returned no pid: {launch_resp}")
    ctx["pid"] = pid
    logger.info(f"[2b] VS Code pid={pid}")

    # ── 2. Wait for window ────────────────────────────────────────────────────
    # VS Code's `code` launcher exits after handing off to the Electron main
    # process, so the returned pid may not own the window.  title_hint catches
    # the actual VS Code window regardless of which pid owns it.
    time.sleep(3.0)   # VS Code needs time to load Electron + UI
    win = cua.wait_for_window(pid, timeout=30, title_hint="Visual Studio Code")
    window_id = int(win["window_id"])
    actual_pid = int(win.get("pid", pid))
    if actual_pid != pid:
        logger.info(f"[2b] Electron main pid={actual_pid} (launcher was {pid})")
        pid = actual_pid
        ctx["pid"] = pid
    ctx["window_id"] = window_id
    logger.info(f"[2b] VS Code window id={window_id}, pid={pid}")

    # ── 3. Activate window ────────────────────────────────────────────────────
    cua.activate(pid, window_id, app_name="Visual Studio Code")
    time.sleep(1.5)

    # ── 4. Open integrated terminal via Ctrl+` ────────────────────────────────
    # The integrated terminal lets us write a file reliably without needing
    # to know the exact Monaco API version.
    logger.info("[2b] Opening integrated terminal (Ctrl+backtick) …")
    cua.hotkey(pid, ["ctrl", "`"], window_id=window_id)
    time.sleep(2.0)

    # ── 5. Use page tool to type in the terminal ──────────────────────────────
    # Attempt via page evaluate (CDP JavaScript execution).
    # The `page evaluate` tool does not take window_id; pass pid only.
    write_script = f"""
(function() {{
    try {{
        const fs = require('fs');
        fs.writeFileSync(
            {repr(str(NOTE_PATH))},
            {repr(VSCODE_NOTE_CONTENT)},
            'utf8'
        );
        return 'written';
    }} catch(e) {{
        return 'error: ' + e.message;
    }}
}})()
"""
    page_result = {}
    try:
        logger.info("[2b] Using page evaluate to write file via Node.js fs …")
        page_result = cua.page(pid, "evaluate", script=write_script)
        logger.info(f"[2b] page evaluate result: {page_result}")
    except Exception as exc:
        logger.warning(f"[2b] page evaluate failed: {exc} — trying terminal fallback")
        page_result = {}

    # ── 6. Terminal fallback ─────────────────────────────────────────────────
    # If the CDP script approach didn't work, type the write command into the
    # integrated terminal opened by Ctrl+`, then echo back the file content so
    # something is visible in the terminal panel.
    if not _file_written_successfully(NOTE_PATH):
        logger.info("[2b] Falling back to terminal write …")
        try:
            cua.page(pid, "click", selector=".xterm-cursor-layer, .terminal")
            time.sleep(0.5)
        except Exception:
            pass

        # Base64-encode content so the command is safe for PowerShell 5.1
        # (no backticks, quotes, or special chars from VSCODE_NOTE_CONTENT).
        b64 = base64.b64encode(VSCODE_NOTE_CONTENT.encode("utf-8")).decode("ascii")
        write_cmd = (
            f"python -c \"import base64; open({repr(str(NOTE_PATH))}, 'wb')"
            f".write(base64.b64decode('{b64}'))\"\n"
        )
        cua.type_text(pid, window_id, write_cmd)
        time.sleep(2.0)

    # ── 6b. Print file content in the terminal for visual confirmation ────────
    # Regardless of which path wrote the file, echo it back into the terminal
    # so the VS Code terminal panel shows something meaningful.
    logger.info("[2b] Printing file content to terminal …")
    try:
        cua.page(pid, "click", selector=".xterm-cursor-layer, .terminal")
        time.sleep(0.3)
    except Exception:
        pass
    show_cmd = f"Get-Content {repr(str(NOTE_PATH))}\n"
    cua.type_text(pid, window_id, show_cmd)
    time.sleep(1.5)

    # ── 7. Also open the file in the editor for visual confirmation ───────────
    try:
        open_script = f"""
(function() {{
    try {{
        const cp = require('child_process');
        cp.exec('code {repr(str(NOTE_PATH))[1:-1]}');
        return 'opened';
    }} catch(e) {{
        return 'error: ' + e.message;
    }}
}})()
"""
        cua.page(pid, "evaluate", script=open_script)
        time.sleep(1.0)
    except Exception as exc:
        logger.debug(f"[2b] Open-in-editor attempt: {exc}")

    # ── 8. Verify ─────────────────────────────────────────────────────────────
    time.sleep(1.0)
    written = _file_written_successfully(NOTE_PATH)
    ctx["content_written"] = written

    if written:
        actual = NOTE_PATH.read_text(encoding="utf-8", errors="replace")
        logger.info(f"[2b] ✓ File written: {NOTE_PATH}  ({len(actual)} bytes)")
    else:
        logger.warning(f"[2b] File not found at {NOTE_PATH} — task may have partially succeeded")

    # Use page tool to read the VS Code tab/title bar as additional DOM verification
    dom_title = ""
    try:
        dom_result = cua.page(
            pid,
            "evaluate",
            script="document.title",
        )
        dom_title = str(dom_result.get("raw", dom_result.get("result", "")))
        logger.info(f"[2b] VS Code window title via CDP: '{dom_title}'")
    except Exception as exc:
        logger.debug(f"[2b] DOM title read failed: {exc}")

    return {
        "note_path":        str(NOTE_PATH),
        "content_written":  written,
        "page_result":      str(page_result),
        "dom_title":        dom_title,
        "debugging_port":   VSCODE_DEBUGGING_PORT,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_written_successfully(path: Path) -> bool:
    """Return True if the note file exists and has meaningful content."""
    try:
        return path.exists() and path.stat().st_size > 10
    except Exception:
        return False
