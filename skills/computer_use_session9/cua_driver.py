"""
CUA Driver adapter.

Wraps every `cua-driver call <tool> <json>` invocation through the
long-running daemon so the element-index cache survives across calls
(daemon invariant from CUA_DRIVER_GUIDE.md §3.2).

API reference: CUA_DRIVER_GUIDE.md §4 — all 34 tools.

Platform notes:
  macOS   — `bring_to_front` is a no-op; use `activate_app_macos()` instead.
  Windows — `bring_to_front` works via SetForegroundWindow.
  Linux   — input synthesis needs X11; Wayland needs portal grant.

Adapter contract: all uncertain or version-locked parameter names are
centralised in this file.  If a future cua-driver release renames a
parameter, update only this file.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import time
from typing import Any

from .config import CUA_DRIVER_PATH

logger = logging.getLogger("cua_driver")
PLATFORM = platform.system()


class CuaError(RuntimeError):
    """Raised when a cua-driver tool call returns a non-zero exit code."""


class CuaDriver:
    """Thin wrapper over `cua-driver call <tool> <json>` through the daemon."""

    def __init__(self, binary: str = CUA_DRIVER_PATH):
        self.binary = binary
        logger.debug(f"CuaDriver using binary: {self.binary}")

    # ── Low-level dispatch ────────────────────────────────────────────────────

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Invoke one cua-driver tool through the running daemon.

        Raises CuaError on non-zero exit.  Returns parsed JSON dict,
        or {"raw": <stdout>} for non-JSON output.
        """
        cmd = [self.binary, "call", tool, json.dumps(args)]
        logger.debug(f"cua call: {tool}  args={args}")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            raise CuaError(
                f"cua-driver binary not found at '{self.binary}'. "
                "Install cua-driver and set CUA_DRIVER_PATH if needed."
            )
        if proc.returncode != 0:
            raise CuaError(
                f"[{tool}] exit {proc.returncode}: {proc.stderr.strip()}"
            )
        raw = proc.stdout.strip()
        if raw.startswith("{") or raw.startswith("["):
            result = json.loads(raw)
            logger.debug(f"cua result ({tool}): {str(result)[:200]}")
            return result
        return {"raw": raw}

    # ── Daemon management ─────────────────────────────────────────────────────

    def ensure_daemon(self) -> None:
        """Start `cua-driver serve` if no daemon is currently running.

        The daemon is required for any element-index operation because the
        element cache lives in daemon memory (CUA_DRIVER_GUIDE.md §3.2).
        """
        try:
            status = subprocess.run(
                [self.binary, "status"], capture_output=True, text=True, timeout=5,
            )
            if "running" in status.stdout.lower():
                logger.info("cua-driver daemon already running")
                return
        except Exception:
            pass
        logger.info("Starting cua-driver daemon …")
        try:
            subprocess.Popen(
                [self.binary, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise CuaError(
                f"cua-driver binary not found at '{self.binary}'.\n"
                "Install cua-driver first:\n"
                "  macOS/Linux: /bin/bash -c \"$(curl -fsSL "
                "https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)\"\n"
                "  Windows: download cua-driver.exe from https://github.com/trycua/cua/releases "
                "and place it on PATH (e.g. C:\\Windows\\System32\\) or set CUA_DRIVER_PATH env var.\n"
                "After install run: cua-driver --version"
            )
        # Wait up to 2 s for the daemon socket to appear.
        for _ in range(8):
            time.sleep(0.25)
            try:
                st = subprocess.run(
                    [self.binary, "status"], capture_output=True, text=True, timeout=3,
                )
                if "running" in st.stdout.lower():
                    logger.info("cua-driver daemon started")
                    return
            except Exception:
                pass
        logger.warning("cua-driver daemon may not be ready — proceeding anyway")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_apps(self) -> dict:
        return self.call("list_apps", {})

    def list_windows(self) -> dict:
        return self.call("list_windows", {})

    def get_accessibility_tree(self) -> dict:
        """Desktop-level snapshot. No TCC grant required."""
        return self.call("get_accessibility_tree", {})

    def get_screen_size(self) -> dict:
        return self.call("get_screen_size", {})

    def get_cursor_position(self) -> dict:
        return self.call("get_cursor_position", {})

    # ── App lifecycle ─────────────────────────────────────────────────────────

    def launch_app(
        self,
        name: str = None,
        bundle_id: str = None,
        urls: list[str] = None,
        additional_arguments: list[str] = None,
        electron_debugging_port: int = None,
        webkit_inspector_port: int = None,
    ) -> dict:
        """Launch an application.

        On macOS use `bundle_id` for reliable identification.
        On Windows use `name` (matches the executable or window title).
        Pass `additional_arguments` to forward CLI args to the launched process.
        Pass `urls` to open URLs in the default browser (Windows: ShellExecuteEx).
        Returns dict with at least {"pid": int} and a "windows" array on Windows.
        """
        args: dict[str, Any] = {}
        if name:
            args["name"] = name
        if bundle_id:
            args["bundle_id"] = bundle_id
        if urls:
            args["urls"] = urls
        if additional_arguments:
            args["additional_arguments"] = additional_arguments
        if electron_debugging_port:
            args["electron_debugging_port"] = electron_debugging_port
        if webkit_inspector_port:
            args["webkit_inspector_port"] = webkit_inspector_port
        return self.call("launch_app", args)

    def kill_app(self, pid: int) -> dict:
        return self.call("kill_app", {"pid": pid})

    # ── Activation (platform-split) ───────────────────────────────────────────

    def activate(self, pid: int, window_id: int, app_name: str = "") -> None:
        """
        Bring the app window to the foreground.

        Windows: bring_to_front works via SetForegroundWindow (§7.5).
        macOS:   bring_to_front is a no-op; use AppleScript instead (§6.1).
        Linux:   bring_to_front is a no-op; try xdotool if available.
        """
        if PLATFORM == "Windows":
            self.bring_to_front(pid, window_id)
        elif PLATFORM == "Darwin":
            self._activate_macos(app_name or "")
        else:
            self._activate_linux(pid)
        time.sleep(1.0)  # let the AX hierarchy stabilise after activation

    def bring_to_front(self, pid: int, window_id: int) -> dict:
        """Windows-only: SetForegroundWindow. No-op on macOS/Linux."""
        return self.call("bring_to_front", {"pid": pid, "window_id": window_id})

    def _activate_macos(self, app_name: str) -> None:
        """macOS: activate via AppleScript (workaround for §6.1 background launch)."""
        if not app_name:
            return
        cmd = ["osascript", "-e", f'tell application "{app_name}" to activate']
        subprocess.run(cmd, check=False, capture_output=True)

    def _activate_linux(self, pid: int) -> None:
        """Linux: attempt xdotool activation if available."""
        subprocess.run(
            ["xdotool", "search", "--pid", str(pid), "--onlyvisible", "windowactivate"],
            check=False, capture_output=True,
        )

    # ── Perception ────────────────────────────────────────────────────────────

    def get_window_state(
        self,
        pid: int,
        window_id: int,
        capture_mode: str = "ax",
        query: str = None,
        screenshot_out_file: str = None,
    ) -> dict:
        """
        Walk the AX / UIA tree for (pid, window_id).

        capture_mode:
          "ax"     — AX tree only, no screenshot (fast, no Screen Recording grant)
          "som"    — AX tree + annotated screenshot
          "vision" — screenshot only (for vision-LM agents)

        Returns {"element_count": N, "tree_markdown": "...", ...}.
        Raises CuaError if element_count == 0 and capture_mode is "ax"
        (empty tree = missing TCC grant or app not activated; §6.6).
        """
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "capture_mode": capture_mode,
        }
        if query:
            args["query"] = query
        if screenshot_out_file:
            args["screenshot_out_file"] = screenshot_out_file

        state = self.call("get_window_state", args)

        if capture_mode == "ax" and state.get("element_count", 0) == 0:
            raise CuaError(
                f"Empty AX tree for pid={pid} wid={window_id}. "
                "Possible causes: missing accessibility grant, app not activated, "
                "or Electron app launched without electron_debugging_port. "
                "Run `cua-driver permissions grant` on macOS; on Windows check UIA."
            )
        return state

    def zoom(
        self,
        pid: int,
        window_id: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> dict:
        return self.call("zoom", {
            "pid": pid, "window_id": window_id,
            "x": x, "y": y, "width": width, "height": height,
        })

    # ── Action ────────────────────────────────────────────────────────────────

    def click(
        self,
        pid: int,
        window_id: int,
        element_index: int = None,
        x: int = None,
        y: int = None,
        modifier: str = None,
        count: int = 1,
    ) -> dict:
        """
        Click a UI element.

        Use element_index (from AX tree scan) for semantic addressing.
        Use (x, y) pixel coordinates for vision-mode or canvas targets.
        Never mix both in the same call.
        """
        args: dict[str, Any] = {"pid": pid, "window_id": window_id}
        if element_index is not None:
            args["element_index"] = element_index
        elif x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        else:
            raise ValueError("click requires either element_index or (x, y)")
        if modifier:
            args["modifier"] = modifier
        if count != 1:
            args["count"] = count
        return self.call("click", args)

    def double_click(
        self,
        pid: int,
        window_id: int,
        element_index: int = None,
        x: int = None,
        y: int = None,
    ) -> dict:
        args: dict[str, Any] = {"pid": pid, "window_id": window_id}
        if element_index is not None:
            args["element_index"] = element_index
        elif x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        return self.call("double_click", args)

    def right_click(
        self,
        pid: int,
        window_id: int,
        element_index: int = None,
        x: int = None,
        y: int = None,
    ) -> dict:
        args: dict[str, Any] = {"pid": pid, "window_id": window_id}
        if element_index is not None:
            args["element_index"] = element_index
        elif x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        return self.call("right_click", args)

    def type_text(self, pid: int, window_id: int, text: str) -> dict:
        """Insert text via AXSetAttribute(kAXSelectedText). More reliable than keystrokes."""
        return self.call("type_text", {"pid": pid, "window_id": window_id, "text": text})

    def press_key(self, pid: int, window_id: int, key: str) -> dict:
        return self.call("press_key", {"pid": pid, "window_id": window_id, "key": key})

    def hotkey(self, pid: int, keys: list[str], window_id: int = None) -> dict:
        """Send a key combination, e.g. keys=["ctrl", "s"]."""
        args: dict[str, Any] = {"pid": pid, "keys": keys}
        if window_id is not None:
            args["window_id"] = window_id
        return self.call("hotkey", args)

    def set_value(self, pid: int, window_id: int, element_index: int, value: str) -> dict:
        """Directly set an AX value (faster than typing for form fields)."""
        return self.call("set_value", {
            "pid": pid, "window_id": window_id,
            "element_index": element_index, "value": value,
        })

    def drag(
        self,
        pid: int,
        window_id: int,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
    ) -> dict:
        return self.call("drag", {
            "pid": pid, "window_id": window_id,
            "from_x": from_x, "from_y": from_y,
            "to_x": to_x, "to_y": to_y,
        })

    def scroll(self, pid: int, window_id: int, direction: str = "down", amount: int = 3) -> dict:
        return self.call("scroll", {"pid": pid, "window_id": window_id, "direction": direction, "amount": amount})

    # ── Browser / Electron page tool ──────────────────────────────────────────

    def page(
        self,
        pid: int,
        action: str,
        selector: str = None,
        value: str = None,
        script: str = None,
        url: str = None,
        timeout_ms: int = 5000,
    ) -> dict:
        """
        Drive Electron / browser DOM via Chrome DevTools Protocol.

        Requires the app to have been launched with `electron_debugging_port`.
        Actions (§4 Browser-internal, §7.2):
          "click"    — click element matching selector
          "type"     — type value into element matching selector
          "evaluate" — evaluate JS script in the renderer, return result
          "navigate" — navigate to url
          "wait"     — wait for selector to appear (up to timeout_ms)

        ADAPTER NOTE: action names and parameter keys are assumed from
        the guide example and CDP conventions.  If cua-driver's actual
        schema differs, update only this method.
        """
        args: dict[str, Any] = {"pid": pid, "action": action}
        if selector:
            args["selector"] = selector
        if value:
            args["value"] = value
        if script:
            args["script"] = script
        if url:
            args["url"] = url
        if timeout_ms != 5000:
            args["timeout_ms"] = timeout_ms
        return self.call("page", args)

    # ── Session & cursor overlay ──────────────────────────────────────────────

    def start_session(self, name: str) -> dict:
        return self.call("start_session", {"name": name})

    def end_session(self) -> dict:
        return self.call("end_session", {})

    def set_agent_cursor_enabled(self, enabled: bool) -> dict:
        return self.call("set_agent_cursor_enabled", {"enabled": enabled})

    def set_agent_cursor_style(self, style: str = "ring") -> dict:
        """style: "ring" | "dot" | "arrow" — check cua-driver describe for valid values."""
        return self.call("set_agent_cursor_style", {"style": style})

    def set_agent_cursor_motion(self, motion: str = "smooth") -> dict:
        return self.call("set_agent_cursor_motion", {"motion": motion})

    def move_cursor(self, x: int, y: int) -> dict:
        return self.call("move_cursor", {"x": x, "y": y})

    def get_agent_cursor_state(self) -> dict:
        return self.call("get_agent_cursor_state", {})

    # ── Recording & replay ────────────────────────────────────────────────────

    def start_recording(self, output_dir: str) -> dict:
        """Begin turn-numbered trajectory recording to output_dir."""
        return self.call("start_recording", {"output_dir": output_dir})

    def stop_recording(self) -> dict:
        return self.call("stop_recording", {})

    def get_recording_state(self) -> dict:
        return self.call("get_recording_state", {})

    def replay_trajectory(self, trajectory_dir: str) -> dict:
        return self.call("replay_trajectory", {"trajectory_dir": trajectory_dir})

    # ── Configuration & introspection ─────────────────────────────────────────

    def check_permissions(self) -> dict:
        return self.call("check_permissions", {})

    def get_config(self) -> dict:
        return self.call("get_config", {})

    # ── High-level helpers ────────────────────────────────────────────────────

    def wait_for_window(
        self,
        pid: int,
        timeout: float = 20.0,
        poll: float = 0.5,
        title_hint: str = None,
    ) -> dict:
        """Poll list_windows until a window for `pid` appears. Returns window dict.

        On Windows, UWP apps (Calculator, Store apps) are hosted by
        ApplicationFrameHost.exe — the launcher pid differs from the window's
        owning pid.  Pass title_hint to also search by window title as a fallback.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.list_windows()
            windows = result.get("windows", [])
            for w in windows:
                if w.get("pid") == pid:
                    return w
            if title_hint:
                for w in windows:
                    title = str(w.get("title", ""))
                    if title_hint.lower() in title.lower():
                        return w
            time.sleep(poll)
        raise CuaError(f"No window appeared for pid={pid} after {timeout:.0f}s")

    def first_window_id(self, pid: int) -> int:
        """Return the first window_id for a given pid."""
        result = self.list_windows()
        for w in result.get("windows", []):
            if w.get("pid") == pid:
                return int(w["window_id"])
        raise CuaError(f"No window found for pid={pid}")
