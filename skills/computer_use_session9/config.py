"""
Configuration for the computer-use Session 9 skill.

All tunable parameters live here. Override via environment variables.
"""

import os
import platform
from pathlib import Path

PLATFORM = platform.system()  # "Windows", "Darwin", "Linux"

# ── cua-driver ────────────────────────────────────────────────────────────────

def _find_cua_binary() -> str:
    env = os.getenv("CUA_DRIVER_PATH")
    if env and Path(env).exists():
        return env
    candidates = [
        Path.home() / ".local" / "bin" / ("cua-driver.exe" if PLATFORM == "Windows" else "cua-driver"),
        Path.home() / ".local" / "bin" / "cua-driver",
        Path("/usr/local/bin/cua-driver"),
        Path("C:/Program Files/cua-driver/cua-driver.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return "cua-driver"  # must be on PATH

CUA_DRIVER_PATH = _find_cua_binary()

# ── LLM Gateway V9 ────────────────────────────────────────────────────────────

GATEWAY_URL = os.getenv("LLM_GATEWAY_V9_URL", "http://localhost:8109")

# ── Trajectories ──────────────────────────────────────────────────────────────

TRAJECTORIES_DIR = Path(os.getenv("TRAJECTORIES_DIR", "trajectories"))

# ── Task 1 — Calculator ───────────────────────────────────────────────────────

CALC_EXPRESSION   = "123 + 456"
CALC_EXPECTED     = "579"

# Windows Calculator (UWP) is launched by name on Win10/11.
# macOS uses the bundle_id.
CALC_APP_NAME     = os.getenv("CALC_APP_NAME", "Calculator")
CALC_BUNDLE_ID    = os.getenv("CALC_BUNDLE_ID", "com.apple.calculator")

# Digit and operator button labels as exposed by UIA / AX.
# Windows UWP Calculator uses full word names; macOS uses symbols.
CALC_BUTTON_LABELS_WINDOWS = {
    "1": ["One", "1"],
    "2": ["Two", "2"],
    "3": ["Three", "3"],
    "4": ["Four", "4"],
    "5": ["Five", "5"],
    "6": ["Six", "6"],
    "+": ["Plus", "Add", "+"],
    "=": ["Equals", "Equal", "="],
}
CALC_BUTTON_LABELS_MACOS = {
    "1": ["1"],
    "2": ["2"],
    "3": ["3"],
    "4": ["4"],
    "5": ["5"],
    "6": ["6"],
    "+": ["+"],
    "=": ["="],
}
CALC_BUTTON_LABELS = (
    CALC_BUTTON_LABELS_WINDOWS if PLATFORM == "Windows"
    else CALC_BUTTON_LABELS_MACOS
)

# ── Task 2 — VS Code ──────────────────────────────────────────────────────────

VSCODE_APP_NAME          = os.getenv("VSCODE_APP_NAME", "code")
VSCODE_DEBUGGING_PORT    = int(os.getenv("VSCODE_DEBUGGING_PORT", "9222"))
VSCODE_NOTE_FILENAME     = "cua_session9_note.md"
VSCODE_NOTE_CONTENT      = (
    "# CUA Session 9 — Computer Use Demonstration\n\n"
    "This file was created by the CUA computer-use skill\n"
    "using the Electron `page` tool (Layer 2b).\n\n"
    "Layer: 2b — Accessibility tree / Page DOM\n"
    "Substrate: cua-driver + Chrome DevTools Protocol\n"
    "Gateway: V9 (http://localhost:8109)\n"
)

# ── Task 3 — Canvas ───────────────────────────────────────────────────────────

CANVAS_HTML_PATH = Path(__file__).parent.parent.parent / "assets" / "canvas_target.html"

# Browser used to open the canvas.  On Windows Edge is typically installed;
# on macOS use Chrome or Safari.  Override via env.
CANVAS_BROWSER = os.getenv(
    "CANVAS_BROWSER",
    "msedge" if PLATFORM == "Windows" else "Google Chrome",
)

# Screenshot temp file (platform-appropriate tmp dir)
import tempfile
SCREENSHOT_TMP = Path(tempfile.gettempdir()) / "cua_session9_screenshot.png"
