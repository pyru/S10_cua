"""
Task 3: Canvas-rendered vision task — Layer 3.
Opens a browser-based canvas target app and uses vision to locate and
click a randomly-positioned red circle drawn inside an HTML <canvas>.

The canvas element renders purely via JavaScript Canvas 2D API.  No ARIA
roles, no DOM-addressable child elements exist for the target position.
AX and CDP both see an opaque <canvas> node.  Vision is the only path.

Cascade path (full escalation visible in code):
  Layer 1  — No direct CLI for canvas interaction. Escalates.
  Layer 2a — Scan AX tree of the browser window.
             AX returns an AXWebArea node with the canvas as a single
             opaque child.  No button/element at the target's position.
             Escalates: "canvas has no AX structure for target location".
  Layer 2b — Try page DOM via CDP.
             The canvas DOM node exists, but its internal drawing (the
             red circle) is not addressable by CSS selector or JS API.
             Escalates: "canvas pixel content not addressable via DOM".
  Layer 3  — Take screenshot via capture_mode="vision".
             POST to V9 /v1/vision with prompt to locate the red circle.
             Parse pixel coordinates from the response.
             Click at those coordinates.
             Verify: take a second screenshot, ask vision if "HIT" text
             or green dot appeared near the target.
  Layer 5  — Recording wraps the entire run.

V9 Gateway note: ALL vision calls go through /v1/vision on port 8109.
"""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import time

from ..cascade import (
    CascadeController,
    CascadeResult,
    EscalateToNextLayer,
    Layer,
)
from ..config import (
    CANVAS_BROWSER,
    CANVAS_HTML_PATH,
    PLATFORM,
    SCREENSHOT_TMP,
)
from ..cua_driver import CuaDriver
from ..gateway_client import GatewayClient
from ..recording import RecordingManager

logger = logging.getLogger("task3_canvas")


def run(cua: CuaDriver, gateway: GatewayClient, recorder: RecordingManager) -> dict:
    """
    Entry point.  Returns {success, hit, coords, trajectory_dir, layer_used}.
    """
    logger.info("=" * 60)
    logger.info("TASK 3: Canvas Vision  |  target=red circle on HTML canvas")
    logger.info("=" * 60)

    gateway.assert_available()
    cua.ensure_daemon()
    traj_dir = recorder.start("vision_canvas")

    cascade = CascadeController(
        task_name="vision_canvas",
        max_layer=Layer.L3_VISION,
    )

    ctx: dict = {}

    steps = [
        (
            Layer.L1_CLI,
            "no CLI path for interactive canvas interaction",
            lambda: _layer1_no_cli(),
        ),
        (
            Layer.L2A_AX,
            "attempt AX scan — expect canvas to be opaque",
            lambda: _layer2a_try_ax(cua, ctx),
        ),
        (
            Layer.L2B_PAGE,
            "attempt CDP page DOM — canvas pixel content not selectable",
            lambda: _layer2b_try_dom(cua, ctx),
        ),
        (
            Layer.L3_VISION,
            "screenshot + V9 vision LLM → pixel coords → click → verify",
            lambda: _layer3_vision(cua, gateway, ctx),
        ),
    ]

    result: CascadeResult = cascade.run(steps)

    try:
        recorder.stop()
        recorder.save_metadata(
            result,
            layer_used=result.layer_used.value,
            extra={
                "hit":         ctx.get("hit"),
                "coords":      ctx.get("coords"),
                "pid":         ctx.get("pid"),
                "window_id":   ctx.get("window_id"),
                "screenshot":  str(SCREENSHOT_TMP),
            },
        )
    except Exception as exc:
        logger.warning(f"Post-task recording cleanup failed: {exc}")

    return {
        "success":        result.success,
        "hit":            ctx.get("hit"),
        "coords":         ctx.get("coords"),
        "layer_used":     result.layer_used.value,
        "trajectory_dir": str(traj_dir),
        "escalations": [
            {"from": e.from_layer, "to": e.to_layer, "reason": e.reason}
            for e in result.escalations
        ],
        "error": result.error,
    }


# ── Layer implementations ─────────────────────────────────────────────────────

def _layer1_no_cli() -> None:
    raise EscalateToNextLayer(
        "No CLI/API can read the canvas pixel content or click a randomly-"
        "positioned target inside a rendered <canvas> element."
    )


def _layer2a_try_ax(cua: CuaDriver, ctx: dict) -> None:
    """
    Layer 2a — AX tree scan.

    Open the canvas page in the browser, then try to read the AX tree.
    A browser's AX tree will expose the <canvas> as an AXWebArea with
    no addressable children for the drawn circle's position.

    We attempt the scan to demonstrate the cascade — then explicitly
    escalate when we confirm the target is not in the tree.
    """
    _open_canvas(cua, ctx)
    pid       = ctx["pid"]
    window_id = ctx["window_id"]

    cua.bring_to_front(pid, window_id)
    time.sleep(2.0)  # let the canvas JS render

    try:
        state = cua.get_window_state(
            pid, window_id, capture_mode="ax", query="canvas",
        )
        tree  = state.get("tree_markdown", "")
        count = state.get("element_count", 0)
        logger.info(f"[2a] AX element_count={count}")
        logger.debug(f"[2a] AX tree snippet:\n{tree[:500]}")

        # The canvas draws its content (the red circle + "▶ TARGET ◀" label) via
        # Canvas 2D API — none of that is exposed as AX nodes. Buttons like
        # "New Target" will appear in the tree but are UI controls, not the
        # click target. Always escalate; the tree is logged for educational value.
        raise EscalateToNextLayer(
            f"AX tree ({count} elements) has no addressable target element. "
            "The <canvas> is opaque to UIA/AX — pixel content (red circle, "
            "▶ TARGET ◀ label) is not exposed as structured accessibility nodes "
            "(CUA_DRIVER_GUIDE.md §7.3). UI buttons like 'New Target' are not "
            "the click target."
        )

    except EscalateToNextLayer:
        raise
    except Exception as exc:
        raise EscalateToNextLayer(
            f"AX scan failed ({exc}) — proceeding to DOM/vision path."
        )


def _layer2b_try_dom(cua: CuaDriver, ctx: dict) -> None:
    """
    Layer 2b — CDP / page DOM.

    Try to locate the canvas target via JavaScript in the page context.
    The <canvas> DOM node exists, but its rendered pixel content (the red
    circle drawn by Canvas 2D API) is not addressable by CSS selector or
    any DOM property.  We can read the canvas's data attribute or check
    the status text — but we cannot determine the circle's position without
    reading pixel data, which would require canvas.getContext('2d').getImageData().

    We attempt this to make the escalation reason explicit in the code.
    """
    pid = ctx.get("pid")
    if not pid:
        raise EscalateToNextLayer("No browser pid — skipping DOM attempt.")

    try:
        # Attempt to read the status text (which IS in the DOM).
        result = cua.page(
            pid,
            "evaluate",
            script="document.getElementById('status').textContent",
        )
        status_text = str(result.get("raw", result.get("result", "")))
        logger.info(f"[2b] DOM status text: '{status_text}'")

        # The status text shows target coordinates — but we need pixel coords
        # in the BROWSER window's coordinate system, not the canvas-local coords.
        # Even if we parse "Target at canvas(350, 200)", we still don't know
        # where that is on screen without the canvas element's bounding rect.
        target_match = re.search(r"Target at canvas\((\d+),\s*(\d+)\)", status_text)
        if target_match:
            canvas_x = int(target_match.group(1))
            canvas_y = int(target_match.group(2))
            logger.info(
                f"[2b] Found target coords in DOM: canvas({canvas_x}, {canvas_y})"
            )
            # We have canvas-local coordinates.  To click, we need screen coords.
            # We can ask the DOM for the canvas element's bounding rect:
            rect_result = cua.page(
                pid,
                "evaluate",
                script="JSON.stringify(document.getElementById('canvas').getBoundingClientRect())",
            )
            rect_text = str(rect_result.get("raw", rect_result.get("result", "{}")))
            logger.info(f"[2b] Canvas bounding rect: {rect_text}")

            import json
            try:
                rect = json.loads(rect_text.strip('"').replace('\\"', '"'))
                screen_x = int(rect.get("left", 0)) + canvas_x
                screen_y = int(rect.get("top", 0)) + canvas_y
                logger.info(
                    f"[2b] Computed screen coords: ({screen_x}, {screen_y}). "
                    "However, these are viewport coords, not window-local pixel coords "
                    "needed by cua-driver click. Escalating to vision for accurate mapping."
                )
            except Exception as e:
                logger.debug(f"[2b] Rect parse failed: {e}")

        raise EscalateToNextLayer(
            "Canvas pixel content (the drawn red circle position) cannot be "
            "reliably addressed via CSS selectors or DOM APIs. The canvas element "
            "is opaque to CDP for structural targeting. Escalating to vision "
            "(CUA_DRIVER_GUIDE.md §7.3: 'HTML <canvas> and WebGL content')."
        )
    except EscalateToNextLayer:
        raise
    except Exception as exc:
        raise EscalateToNextLayer(
            f"DOM/CDP approach failed ({exc}). Escalating to vision."
        )


def _layer3_vision(cua: CuaDriver, gateway: GatewayClient, ctx: dict) -> dict:
    """
    Layer 3 — Vision-based perception and action.

    1. Take screenshot via capture_mode="vision".
    2. POST to V9 /v1/vision with prompt to locate the red circle.
    3. Parse pixel coordinates (x, y) from the LLM response.
    4. Click at (x, y) in window-local coordinates.
    5. Take a verification screenshot.
    6. POST to V9 /v1/vision again to verify a hit was registered.
    """
    pid       = ctx["pid"]
    window_id = ctx["window_id"]

    # ── 1. Screenshot ─────────────────────────────────────────────────────────
    logger.info("[3] Bringing browser to front and taking screenshot …")
    cua.bring_to_front(pid, window_id)
    time.sleep(1.0)  # let the window paint before capture
    SCREENSHOT_TMP.parent.mkdir(parents=True, exist_ok=True)
    snap = cua.get_window_state(pid, window_id, capture_mode="vision")
    _write_screenshot(snap, SCREENSHOT_TMP)
    logger.info(f"[3] Screenshot saved: {SCREENSHOT_TMP}  ({SCREENSHOT_TMP.stat().st_size} bytes)")

    # ── 2. Vision call — locate target ───────────────────────────────────────
    locate_prompt = (
        "This is a screenshot of a browser window showing a canvas drawing application. "
        "There is a RED CIRCLE (with a yellow border) drawn on a dark canvas background. "
        "Find the center of that red circle and return its pixel coordinates in the "
        "FULL IMAGE (not canvas-relative). "
        "Return JSON only: {\"x\": <int>, \"y\": <int>, \"confidence\": \"high|medium|low\"}"
    )
    coord_schema = {
        "type": "object",
        "properties": {
            "x":          {"type": "integer"},
            "y":          {"type": "integer"},
            "confidence": {"type": "string"},
        },
        "required": ["x", "y"],
    }
    logger.info("[3] Calling V9 /v1/vision to locate red circle …")
    coords_raw = gateway.vision_structured(
        str(SCREENSHOT_TMP),
        locate_prompt,
        schema=coord_schema,
        schema_name="target_coords",
        agent="computer_use_vision_task3",
    )
    logger.info(f"[3] Vision response (locate): {coords_raw}")

    x = coords_raw.get("x")
    y = coords_raw.get("y")
    confidence = coords_raw.get("confidence", "unknown")
    if x is None or y is None:
        raise RuntimeError(
            f"Vision LLM did not return coordinates. Raw response: {coords_raw}"
        )
    ctx["coords"] = {"x": x, "y": y, "confidence": confidence}
    logger.info(f"[3] Target located at ({x}, {y}) with confidence={confidence}")

    # ── 3. Click ──────────────────────────────────────────────────────────────
    logger.info(f"[3] Clicking at ({x}, {y}) (pixel coordinates, no element_index) …")
    cua.click(pid, window_id, x=x, y=y)
    time.sleep(1.0)

    # ── 4. Verification screenshot ────────────────────────────────────────────
    verify_path = SCREENSHOT_TMP.parent / "cua_session9_verify.png"
    logger.info("[3] Taking verification screenshot …")
    cua.bring_to_front(pid, window_id)
    time.sleep(0.5)
    verify_snap = cua.get_window_state(pid, window_id, capture_mode="vision")
    _write_screenshot(verify_snap, verify_path)

    # ── 5. Vision call — verify hit ───────────────────────────────────────────
    verify_prompt = (
        "This is a screenshot of a browser canvas application after an agent clicked on it. "
        "Check the canvas and the status bar text at the top. "
        "Did the click register as a HIT on the red target circle? "
        "A HIT shows '✓ HIT!' or 'HIT' in the status bar, or a green dot appeared. "
        "A MISS shows '✗ MISS' or only an orange dot without green. "
        "Return JSON: {\"hit\": true|false, \"status_text\": \"<exact status bar text>\", "
        "\"explanation\": \"<brief reason>\"}"
    )
    hit_schema = {
        "type": "object",
        "properties": {
            "hit":         {"type": "boolean"},
            "status_text": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["hit"],
    }
    image_for_verify = str(verify_path) if verify_path.exists() else str(SCREENSHOT_TMP)
    logger.info("[3] Calling V9 /v1/vision to verify hit …")
    verify_raw = gateway.vision_structured(
        image_for_verify,
        verify_prompt,
        schema=hit_schema,
        schema_name="hit_verification",
        agent="computer_use_vision_verify",
    )
    logger.info(f"[3] Vision response (verify): {verify_raw}")

    hit           = bool(verify_raw.get("hit", False))
    status_text   = verify_raw.get("status_text", "")
    explanation   = verify_raw.get("explanation", "")
    ctx["hit"]    = hit
    logger.info(f"[3] Verification: hit={hit}  status='{status_text}'  reason='{explanation}'")

    return {
        "hit":          hit,
        "coords":       {"x": x, "y": y},
        "confidence":   confidence,
        "status_text":  status_text,
        "explanation":  explanation,
        "screenshot":   str(SCREENSHOT_TMP),
        "verify_shot":  str(verify_path),
    }


# ── Screenshot helper ─────────────────────────────────────────────────────────

def _write_screenshot(state: dict, out_path) -> None:
    """Decode screenshot_png_b64 from a get_window_state response and write to out_path."""
    b64 = state.get("screenshot_png_b64")
    if not b64:
        raise RuntimeError(
            f"get_window_state(vision) returned no screenshot_png_b64. "
            f"Keys present: {list(state.keys())}"
        )
    SCREENSHOT_TMP.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))


# ── Browser open helper ────────────────────────────────────────────────────────

def _open_canvas(cua: CuaDriver, ctx: dict) -> None:
    """
    Open the canvas HTML in the default browser and wait for the window.
    Fills ctx["pid"] and ctx["window_id"].
    """
    html_path = CANVAS_HTML_PATH
    if not html_path.exists():
        raise RuntimeError(
            f"Canvas HTML not found: {html_path}. "
            "Make sure assets/canvas_target.html is present."
        )

    if ctx.get("pid"):
        return  # already open

    file_url = html_path.as_uri()
    logger.info(f"[canvas] Opening {html_path} in browser ({CANVAS_BROWSER}) …")

    pid        = None
    window_id  = None

    # Primary: launch browser with the URL as a CLI argument — cua-driver
    # returns pid + windows array immediately on Windows.
    try:
        resp = cua.launch_app(
            name=CANVAS_BROWSER,
            additional_arguments=[file_url],
        )
        pid = resp.get("pid")
        windows = resp.get("windows", [])
        if windows:
            window_id = int(windows[0]["window_id"])
            logger.info(f"[canvas] Browser launched: pid={pid} wid={window_id}")
    except Exception as exc:
        logger.debug(f"[canvas] launch_app failed ({exc}), falling back to OS open …")
        _os_open_browser(file_url)
        time.sleep(3.0)

    # If no window yet, scan for the browser window by title.
    if not window_id:
        time.sleep(3.0)
        win_list = cua.list_windows().get("windows", [])
        for w in win_list:
            title = (w.get("title") or "").lower()
            if "canvas" in title or "cua" in title:
                pid       = w["pid"]
                window_id = int(w["window_id"])
                logger.info(f"[canvas] Detected browser by title: pid={pid} wid={window_id}")
                break
        # Wider fallback: any browser window
        if not window_id:
            for w in win_list:
                title = (w.get("title") or "").lower()
                if "edge" in title or "chrome" in title or "firefox" in title:
                    pid       = w["pid"]
                    window_id = int(w["window_id"])
                    logger.info(f"[canvas] Detected browser by name: pid={pid} wid={window_id}")
                    break

    if not pid or not window_id:
        raise RuntimeError(
            "Could not determine browser pid/window_id after opening canvas HTML. "
            f"CANVAS_BROWSER={CANVAS_BROWSER!r} file_url={file_url!r}"
        )

    ctx["pid"]       = pid
    ctx["window_id"] = window_id


def _os_open_browser(url: str) -> int:
    """Use the OS shell to open a URL; return 0 (pid unknown)."""
    if PLATFORM == "Windows":
        subprocess.Popen(["cmd", "/c", "start", "", url])
    elif PLATFORM == "Darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])
    time.sleep(2.0)
    return 0  # pid unknown via shell open
