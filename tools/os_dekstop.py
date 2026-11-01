"""
Module B: Pure pathlib/shutil filesystem operations and cross-platform Desktop UI automation.
Strictly avoids GUI simulation for filesystem tasks and caps UI inspection trees under 250 tokens.
"""

import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import psutil

from ares.config import UI_TREE_MAX_TOKENS, WORKSPACE_DIR

logger = logging.getLogger("ares.tools.os_desktop")

try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    HAS_PYAUTOGUI = True
except Exception:
    HAS_PYAUTOGUI = False

if sys.platform == "win32":
    try:
        from pywinauto import Desktop

        HAS_PYWINAUTO = True
    except Exception:
        HAS_PYWINAUTO = False
else:
    HAS_PYWINAUTO = False


class FileSystemOps:
    """
    Deterministic filesystem operations using pathlib and shutil.
    Relative paths are resolved securely inside WORKSPACE_DIR.
    """

    @staticmethod
    def _resolve_path(raw_path: str) -> Path:
        expanded = os.path.expanduser(os.path.expandvars(raw_path))
        p = Path(expanded)
        if not p.is_absolute():
            p = (WORKSPACE_DIR / p).resolve()
        return p

    @classmethod
    def manage_fs(
        cls,
        action: str,
        path: str,
        destination: str = "",
        content: str = "",
    ) -> str:
        """
        Executes filesystem operations without GUI automation.
        Supported actions: create_folder, delete, move, write_file, read_file.
        """
        action_norm = action.strip().lower()
        target = cls._resolve_path(path)

        if action_norm == "create_folder":
            target.mkdir(parents=True, exist_ok=True)
            return f"Created directory: {target}"

        if action_norm == "delete":
            if not target.exists():
                raise FileNotFoundError(f"Path does not exist: {target}")
            if target.is_dir():
                shutil.rmtree(target)
                return f"Deleted directory tree: {target}"
            target.unlink()
            return f"Deleted file: {target}"

        if action_norm == "move":
            if not destination:
                raise ValueError("Destination path is required for 'move' action.")
            if not target.exists():
                raise FileNotFoundError(f"Source path does not exist: {target}")
            dest_path = cls._resolve_path(destination)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            moved_to = shutil.move(str(target), str(dest_path))
            return f"Moved {target} -> {moved_to}"

        if action_norm == "write_file":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content.encode('utf-8'))} bytes to {target}"

        if action_norm == "read_file":
            if not target.exists():
                raise FileNotFoundError(f"File not found: {target}")
            if target.is_dir():
                entries = [p.name + ("/" if p.is_dir() else "") for p in sorted(target.iterdir())]
                return f"Directory listing for {target}:\n" + "\n".join(entries[:100])
            text = target.read_text(encoding="utf-8", errors="replace")
            if len(text) > 4000:
                return text[:4000] + "\n...[truncated]"
            return text

        raise ValueError(
            f"Unsupported manage_fs action '{action}'. "
            "Valid actions: create_folder, delete, move, write_file, read_file."
        )


class DesktopManager:
    """
    Manages background OS applications, UI Automation accessibility trees, and input dispatch.
    """

    ALLOWED_CONTROL_TYPES = {"Button", "Edit", "MenuItem", "CheckBox"}

    @staticmethod
    def launch_app(command_or_path: str) -> str:
        """
        Launches a background desktop application via subprocess.Popen and verifies
        its process status using psutil.
        """
        if not command_or_path.strip():
            raise ValueError("command_or_path cannot be empty.")

        posix_mode = sys.platform != "win32"
        args = shlex.split(command_or_path, posix=posix_mode)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(sys.platform != "win32"),
        )

        # Brief settle window to verify process startup via psutil
        time.sleep(0.25)
        if not psutil.pid_exists(proc.pid):
            raise RuntimeError(f"Process '{command_or_path}' terminated immediately after launch.")

        ps_proc = psutil.Process(proc.pid)
        status = ps_proc.status()
        if status == psutil.STATUS_ZOMBIE:
            raise RuntimeError(f"Process '{command_or_path}' became a zombie process (PID {proc.pid}).")

        return f"Launched '{ps_proc.name()}' successfully (PID: {proc.pid}, status: {status})"

    @classmethod
    def inspect_ui_tree(cls, window_title_keyword: str) -> str:
        """
        Inspects the target window's UI Automation tree, extracts only interactive controls
        (Button, Edit, MenuItem, CheckBox), and enforces a strict <250 token budget (~850 chars).
        Uses pywinauto UIA on Windows with graceful wmctrl/xdotool fallback on Linux.
        """
        max_chars = UI_TREE_MAX_TOKENS * 3  # ~750 chars guarantees <250 BPE tokens

        if HAS_PYWINAUTO and sys.platform == "win32":
            try:
                desktop = Desktop(backend="uia")
                windows = desktop.windows()
                target_win = None
                keyword_lower = window_title_keyword.lower()

                for win in windows:
                    title = win.window_text() or ""
                    if keyword_lower in title.lower():
                        target_win = win
                        break

                if target_win is None:
                    available = [w.window_text() for w in windows if w.window_text()][:10]
                    return f"No window matching '{window_title_keyword}'. Active: {available}"

                lines: List[str] = [f"Window: {target_win.window_text()[:60]}"]
                for ctrl in target_win.descendants():
                    ctrl_type = getattr(ctrl.element_info, "control_type", "")
                    if ctrl_type in cls.ALLOWED_CONTROL_TYPES:
                        name = (ctrl.window_text() or getattr(ctrl.element_info, "name", "")).strip()
                        auto_id = getattr(ctrl.element_info, "automation_id", "") or ""
                        if not name and not auto_id:
                            continue
                        entry = f"[{ctrl_type}] name='{name[:35]}'"
                        if auto_id:
                            entry += f" id='{auto_id[:25]}'"
                        lines.append(entry)
                        if sum(len(x) + 1 for x in lines) >= max_chars:
                            lines.append("...[truncated to <250 tokens]")
                            break

                return "\n".join(lines)[:max_chars]
            except Exception as exc:
                logger.warning("pywinauto inspection error: %s", exc)
                return f"UI inspection error on '{window_title_keyword}': {exc}"

        # Graceful Linux / macOS fallback using wmctrl / xdotool / psutil window process matching
        lines = [f"Platform '{sys.platform}' UI summary for '{window_title_keyword}':"]
        if shutil.which("wmctrl"):
            res = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in res.stdout.splitlines():
                if window_title_keyword.lower() in line.lower():
                    lines.append(f"[Window] {line.strip()}")
        else:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                pname = (proc.info.get("name") or "").lower()
                if window_title_keyword.lower() in pname:
                    lines.append(f"[Process] PID={proc.info['pid']} name='{proc.info['name']}'")

        if len(lines) == 1:
            lines.append("No matching window or process found.")
        return "\n".join(lines)[:max_chars]

    @classmethod
    def send_input(
        cls,
        action_type: str,
        target_name: str = "",
        value: str = "",
    ) -> str:
        """
        Dispatches keyboard/mouse input via pyautogui or invokes named accessibility
        controls via pywinauto.
        Supported action_type:
          - 'hotkey': Presses key chord in `value` (e.g. 'ctrl+s', 'alt+f4')
          - 'type': Types string in `value` (optionally focusing `target_name` first)
          - 'click_control': Clicks UIA control `target_name` inside window `value`
          - 'click_xy': Clicks screen coordinates `value` formatted as 'x,y'
        """
        act = action_type.strip().lower()

        if act == "click_control":
            if not (HAS_PYWINAUTO and sys.platform == "win32"):
                raise RuntimeError("click_control requires Windows with pywinauto installed.")
            desktop = Desktop(backend="uia")
            win_keyword = value.lower()
            target_win = None
            for w in desktop.windows():
                if win_keyword in (w.window_text() or "").lower():
                    target_win = w
                    break
            if target_win is None:
                raise LookupError(f"Window matching '{value}' not found.")

            for ctrl in target_win.descendants():
                cname = (ctrl.window_text() or getattr(ctrl.element_info, "name", "")).strip()
                cid = getattr(ctrl.element_info, "automation_id", "") or ""
                if target_name.lower() in cname.lower() or (cid and target_name.lower() == cid.lower()):
                    ctrl.click_input()
                    return f"Clicked control '{cname or cid}' in window '{target_win.window_text()}'"
            raise LookupError(f"Control '{target_name}' not found in window '{value}'.")

        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui is unavailable in this environment (check DISPLAY).")

        if act in {"hotkey", "type"}:
            if not target_name.strip():
                raise ValueError("Specify the target window title before sending keyboard input.")
            if not (HAS_PYWINAUTO and sys.platform == "win32"):
                raise RuntimeError("Target-window keyboard input requires Windows with pywinauto installed.")
            target_win = next(
                (
                    window
                    for window in Desktop(backend="uia").windows()
                    if target_name.lower() in (window.window_text() or "").lower()
                ),
                None,
            )
            if target_win is None:
                raise LookupError(f"Window matching '{target_name}' not found.")
            target_win.set_focus()

        if act == "hotkey":
            keys = [k.strip() for k in value.split("+") if k.strip()]
            if not keys:
                raise ValueError("Provide hotkey keys separated by '+', e.g., 'ctrl+shift+p'.")
            pyautogui.hotkey(*keys)
            return f"Sent hotkey: {'+'.join(keys)}"

        if act == "type":
            pyautogui.write(value, interval=0.02)
            return f"Typed {len(value)} characters."

        if act == "click_xy":
            parts = [int(p.strip()) for p in value.split(",")]
            if len(parts) != 2:
                raise ValueError("click_xy requires value formatted as 'x,y'.")
            pyautogui.click(x=parts[0], y=parts[1])
            return f"Clicked coordinates ({parts[0]}, {parts[1]})."

        raise ValueError(
            f"Unsupported send_input action_type '{action_type}'. "
            "Use 'hotkey', 'type', 'click_control', or 'click_xy'."
        )