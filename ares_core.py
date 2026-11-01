"""
C:\\Users\\abhishek\\ares\\ares_core.py

ARES Core Engine (Consolidated Root Module):
- ModelManager: Sequential single-model VRAM manager with mandatory 400ms CUDA eviction sleep.
- SecurityGuardrails: System directory blacklist, send2trash deletion, dangerous bash regex blocker.
- ToolExecutor: manage_fs, execute_bash, open_app, open_in_vscode, verify_and_run, download_and_open.
- ReActEngine: 16-step autonomous loop, Markdown JSON regex parser, 3B debugger escalation,
  DeepSeek-R1 loop recovery, and 0.5B context compaction.
"""

import json
import ipaddress
import logging
import os
import re
import shlex
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil
import requests
from email.message import EmailMessage
from send2trash import send2trash

logger = logging.getLogger("ares_core")

# ============================================================================
# 1. SYSTEM CONFIGURATION & MODEL MATRIX
# ============================================================================

ROOT_DIR: Path = Path(__file__).resolve().parent
WORKSPACE_DIR: Path = ROOT_DIR / "workspace"
DOWNLOADS_DIR: Path = Path.home() / "Downloads"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_BASE_URL: str = os.environ.get("ARES_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_GENERATE_URL: str = f"{OLLAMA_BASE_URL}/api/generate"
OLLAMA_CHAT_URL: str = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_PS_URL: str = f"{OLLAMA_BASE_URL}/api/ps"
OLLAMA_TAGS_URL: str = f"{OLLAMA_BASE_URL}/api/tags"

MODEL_ORCHESTRATOR: str = "qwen2.5-coder:1.5b"
MODEL_DEBUGGER: str = "qwen2.5-coder:3b"
MODEL_REASONER: str = "deepseek-r1:1.5b"
MODEL_COMPACTOR: str = "qwen2.5:0.5b"

MODEL_VRAM_MB: Dict[str, int] = {
    MODEL_ORCHESTRATOR: 1120,
    MODEL_DEBUGGER: 1950,
    MODEL_REASONER: 1120,
    MODEL_COMPACTOR: 390,
}

DEFAULT_NUM_CTX: int = 4096
COMPACTION_TOKEN_LIMIT: int = 3500
DEFAULT_TEMPERATURE: float = 0.0
MAX_REACT_STEPS: int = 16
SUBPROCESS_TIMEOUT: int = 30
VRAM_UNLOAD_BUFFER_SECONDS: float = 0.4

STOP_TOKENS: List[str] = [
    "Observation:",
    "```\nObservation",
    "<|im_end|>",
    "<|endoftext|>",
]

EventCallback = Callable[[Dict[str, Any]], None]


# ============================================================================
# 2. SECURITY GUARDRAILS
# ============================================================================

class SecurityGuardrails:
    """
    Enforces strict path blacklists, non-destructive deletions, and shell command filtering.
    """

    WINDOWS_BLACKLIST_PREFIXES: Tuple[str, ...] = (
        r"c:\windows",
        r"c:\program files",
        r"c:\program files (x86)",
        r"c:\programdata",
        r"c:\system volume information",
        r"c:\$recycle.bin",
        r"c:\recovery",
        r"c:\boot",
    )

    POSIX_BLACKLIST_PREFIXES: Tuple[str, ...] = (
        "/etc",
        "/bin",
        "/sbin",
        "/usr",
        "/var",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
        "/root",
        "/lib",
        "/lib64",
    )

    DANGEROUS_BASH_REGEXES: Tuple[re.Pattern[str], ...] = (
        re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*|--recursive|--force)\b", re.IGNORECASE),
        re.compile(r"\b(del|erase|rd|rmdir|Remove-Item)\b", re.IGNORECASE),
        re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
        re.compile(r"\b(mkfs|fdisk|diskpart|bcdedit|cipher\s+\/w|Clear-Disk|Format-Volume)\b", re.IGNORECASE),
        re.compile(r"\breg\s+(delete|add)\b", re.IGNORECASE),
        re.compile(r"\b(sc\s+(config|create|delete|stop)|Stop-Process|taskkill|Set-ExecutionPolicy)\b", re.IGNORECASE),
        re.compile(r"\bdd\s+if=", re.IGNORECASE),
        re.compile(r"\b(shutdown|reboot|poweroff|halt|init\s+[06])\b", re.IGNORECASE),
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        re.compile(r"\bRemove-Item\b.*(-Recurse|-Force)", re.IGNORECASE),
        re.compile(r"\b(takeown|icacls)\b.*\/grant", re.IGNORECASE),
    )

    @classmethod
    def _protected_windows_paths(cls) -> List[str]:
        protected = list(cls.WINDOWS_BLACKLIST_PREFIXES)
        for variable in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            value = os.environ.get(variable)
            if value:
                protected.append(value.replace("/", "\\").rstrip("\\").lower())
        return protected

    @classmethod
    def resolve_safe_path(cls, raw_path: str, for_write_or_delete: bool = True) -> Path:
        """
        Resolves a path relative to WORKSPACE_DIR if not absolute, and verifies it does not
        target root drives or blacklisted OS directories.
        """
        if not raw_path or not raw_path.strip():
            raise ValueError("Target path cannot be empty.")

        expanded = os.path.expanduser(os.path.expandvars(raw_path.strip()))
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = (WORKSPACE_DIR / candidate).resolve()
        else:
            candidate = candidate.resolve()

        norm_str = str(candidate).replace("/", "\\").rstrip("\\").lower()
        posix_str = str(candidate).replace("\\", "/").rstrip("/").lower()

        # Block root filesystem drives (e.g., "C:", "C:\", "/")
        if norm_str in ("c:", "d:", "e:", "") or posix_str == "":
            raise PermissionError(f"Guardrail Violation: Root drive access is prohibited ({candidate}).")

        if candidate.anchor and candidate == Path(candidate.anchor).resolve():
            raise PermissionError(f"Guardrail Violation: Root directory operation prohibited ({candidate}).")

        for prefix in cls._protected_windows_paths():
            if norm_str == prefix or norm_str.startswith(prefix + "\\"):
                raise PermissionError(
                    f"Guardrail Violation: Access to protected Windows directory '{candidate}' is blocked."
                )

        for prefix in cls.POSIX_BLACKLIST_PREFIXES:
            if posix_str == prefix or posix_str.startswith(prefix + "/"):
                raise PermissionError(
                    f"Guardrail Violation: Access to protected system directory '{candidate}' is blocked."
                )

        if for_write_or_delete and candidate == ROOT_DIR:
            raise PermissionError("Guardrail Violation: Cannot delete or overwrite the ARES root folder.")

        return candidate

    @classmethod
    def validate_bash_command(cls, command: str) -> None:
        """Blocks destructive shell commands via compiled regular expressions."""
        if not command or not command.strip():
            raise ValueError("Bash command cannot be empty.")

        for pattern in cls.DANGEROUS_BASH_REGEXES:
            if pattern.search(command):
                raise PermissionError(
                    f"Guardrail Violation: Command blocked by security pattern '{pattern.pattern}'."
                )

        cmd_lower = command.lower().replace("/", "\\")
        mutating_command = re.search(
            r"\b(del|erase|rm|rd|rmdir|remove-item|move|ren|copy|xcopy|robocopy|set-content|add-content|out-file)\b|>>?",
            cmd_lower,
        )
        for protected in cls._protected_windows_paths():
            if protected in cmd_lower and mutating_command:
                raise PermissionError(
                    f"Guardrail Violation: Command attempts to modify protected system path '{protected}'."
                )

# ============================================================================
# 3. OLLAMA SEQUENTIAL MODEL MANAGER
# ============================================================================

class ModelManager:
    """
    Controls sequential single-model residency on a 4GB VRAM budget.
    Enforces a mandatory 400ms sleep buffer on keep_alive=0 unloads to prevent CUDA OOM.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        event_callback: Optional[EventCallback] = None,
    ) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.currently_loaded_model: Optional[str] = None
        self.event_callback: Optional[EventCallback] = event_callback
        self.session: requests.Session = requests.Session()

    def _emit_vram_state(self, status: str, active_model: Optional[str]) -> None:
        vram_mb = MODEL_VRAM_MB.get(active_model or "", 0)
        if self.event_callback:
            self.event_callback(
                {
                    "type": "vram",
                    "status": status,
                    "active_model": active_model or "None (Idle)",
                    "vram_mb": vram_mb,
                    "vram_ceiling_mb": 3200,
                }
            )

    def get_running_models(self) -> List[str]:
        """Queries Ollama /api/ps for currently loaded VRAM models."""
        try:
            resp = self.session.get(f"{self.base_url}/api/ps", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [m.get("name", "") for m in models if m.get("name")]
        except requests.RequestException:
            return []

    def unload_model(self, model_name: str) -> bool:
        """
        Immediately flushes `model_name` from VRAM via keep_alive=0 and waits
        a mandatory 400ms (`time.sleep(0.4)`) for CUDA page reclamation.
        """
        if not model_name:
            return True

        self._emit_vram_state(f"Evicting {model_name} (keep_alive=0)...", model_name)
        payload: Dict[str, Any] = {
            "model": model_name,
            "prompt": "",
            "keep_alive": 0,
            "stream": False,
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to unload model %s: %s", model_name, exc)
        finally:
            # Mandatory 400ms hardware settle delay to prevent CUDA OOM crashes
            time.sleep(VRAM_UNLOAD_BUFFER_SECONDS)
            if self.currently_loaded_model == model_name:
                self.currently_loaded_model = None
            self._emit_vram_state("VRAM Flushed", None)

        return True

    def unload_all(self) -> None:
        """Flushes all daemon-resident models from GPU VRAM."""
        active = set(self.get_running_models())
        if self.currently_loaded_model:
            active.add(self.currently_loaded_model)
        for model in active:
            self.unload_model(model)
        self.currently_loaded_model = None

    def switch_to(self, model_name: str, keep_alive: str = "5m") -> None:
        """
        Ensures strictly one model is resident in VRAM.
        Unloads any other model first (with 400ms sleep buffer), then loads `model_name`.
        """
        resident_models = self.get_running_models()
        for running in resident_models:
            if running != model_name:
                self.unload_model(running)

        if self.currently_loaded_model and self.currently_loaded_model != model_name:
            self.unload_model(self.currently_loaded_model)

        if self.currently_loaded_model != model_name:
            self._emit_vram_state(f"Loading {model_name}...", model_name)
            warmup_payload: Dict[str, Any] = {
                "model": model_name,
                "prompt": "",
                "keep_alive": keep_alive,
                "stream": False,
                "options": {
                    "num_ctx": DEFAULT_NUM_CTX,
                    "temperature": DEFAULT_TEMPERATURE,
                },
            }
            resp = self.session.post(
                f"{self.base_url}/api/generate",
                json=warmup_payload,
                timeout=120,
            )
            resp.raise_for_status()
            self.currently_loaded_model = model_name
            self._emit_vram_state("Active", model_name)

    @staticmethod
    def estimate_tokens(messages: List[Dict[str, str]]) -> int:
        """Fast CPU token estimator for ChatML messages."""
        total_chars = sum(len(m.get("role", "")) + len(m.get("content", "")) + 24 for m in messages)
        return total_chars // 3

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = MODEL_ORCHESTRATOR,
        num_ctx: int = DEFAULT_NUM_CTX,
        temperature: float = DEFAULT_TEMPERATURE,
        keep_alive: str = "5m",
    ) -> str:
        """
        Switches VRAM to `model`, executes deterministic ChatML generation (`temperature=0.0`),
        and strips stop tokens.
        """
        self.switch_to(model, keep_alive=keep_alive)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "stop": STOP_TOKENS,
            },
        }
        resp = self.session.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        content: str = resp.json().get("message", {}).get("content", "")

        for token in STOP_TOKENS:
            if token in content:
                content = content.split(token)[0]
        return content.strip()


# ============================================================================
# 4. TOOL EXECUTOR (GUARDRAILED OS, FS, VSCODE, SUBPROCESS & BROWSER)
# ============================================================================

class ToolExecutor:
    """
    Executes guardrailed local operations with non-destructive deletions (`send2trash`),
    30-second timeouts, and `stdin=subprocess.DEVNULL`.
    """

    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 ARES/2.0"
    )

    @classmethod
    def get_tool_schemas(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "manage_fs",
                "description": (
                    "Safe filesystem manager. Actions: 'create_folder', 'delete' (moves to Recycle Bin "
                    "via send2trash), 'move', 'write_file', 'read_file', 'list_dir'."
                ),
                "args": {
                    "action": "str (create_folder | delete | move | write_file | read_file | list_dir)",
                    "path": "str (target path for create_folder; source path for other actions)",
                    "destination": "str (optional destination path for 'move' only)",
                    "content": "str (optional file content for 'write_file')",
                },
            },
            {
                "name": "execute_bash",
                "description": "Execute a non-destructive shell command with a 30s timeout.",
                "args": {
                    "command": "str (terminal command to execute)",
                },
            },
            {
                "name": "open_app",
                "description": "Launch a desktop application or executable and verify startup via psutil.",
                "args": {
                    "command_or_path": "str (application name, executable path, or file to launch)",
                },
            },
            {
                "name": "open_in_vscode",
                "description": "Open a file in VS Code at a specific line and column using `code -g`.",
                "args": {
                    "path": "str (file path)",
                    "line": "int (optional 1-based line number, default 1)",
                    "col": "int (optional 1-based column number, default 1)",
                },
            },
            {
                "name": "verify_and_run",
                "description": (
                    "Optionally write Python code to `file_path`, run static `py_compile` check, "
                    "and execute the script in an isolated subprocess (timeout=30s)."
                ),
                "args": {
                    "file_path": "str (path to .py script)",
                    "code_content": "str (optional complete Python code to write before running)",
                },
            },
            {
                "name": "download_and_open",
                "description": (
                    "Stream-download a URL directly into ~/Downloads and optionally open it "
                    "with the default OS viewer."
                ),
                "args": {
                    "url": "str (http/https URL to download or open)",
                    "output_name": "str (optional filename in ~/Downloads)",
                    "auto_open": "bool (optional, default True)",
                },
            },
            {
                "name": "open_web_url",
                "description": "Open a public HTTP or HTTPS URL in the default browser.",
                "args": {"url": "str (public web URL)"},
            },
            {
                "name": "fetch_webpage",
                "description": "Fetch and return a bounded text or JSON response from a public HTTP or HTTPS URL.",
                "args": {"url": "str (public web URL)"},
            },
            {
                "name": "download_file",
                "description": "Download one file from a public HTTP or HTTPS URL to the Downloads folder (50 MB maximum).",
                "args": {
                    "url": "str (public file URL)",
                    "output_name": "str (optional filename)",
                },
            },
            {
                "name": "set_system_setting",
                "description": "Set the system volume, mute state, or display brightness.",
                "args": {
                    "setting": "str (volume | mute | brightness)",
                    "value": "number 0-100 for volume/brightness, boolean for mute",
                },
            },
            {
                "name": "open_settings_page",
                "description": "Open an allowlisted Windows settings page.",
                "args": {
                    "section": "str (display | sound | bluetooth | network | apps | personalization | time | privacy)",
                },
            },
            {
                "name": "inspect_window",
                "description": "List interactive controls in a desktop window by title keyword.",
                "args": {"window_title_keyword": "str"},
            },
            {
                "name": "send_input",
                "description": "Type text, send an allowlisted hotkey, or click a named UI control in a window.",
                "args": {
                    "action_type": "str (type | hotkey | click_control)",
                    "target_name": "str (control name for click_control)",
                    "value": "str (text, hotkey, or window title for click_control)",
                },
            },
            {
                "name": "create_email_draft",
                "description": "Create a local .eml email draft; this never sends email.",
                "args": {
                    "to": "str (recipient address or comma-separated addresses)",
                    "subject": "str",
                    "body": "str",
                },
            },
            {
                "name": "finish",
                "description": "Conclude the task and return the final verified answer to the user.",
                "args": {
                    "final_answer": "str (comprehensive summary of completed work)",
                },
            },
        ]

    @classmethod
    def manage_fs(
        cls,
        action: str,
        path: str,
        destination: str = "",
        content: str = "",
    ) -> Dict[str, Any]:
        """
        Pure pathlib/shutil/send2trash filesystem handler protected by SecurityGuardrails.
        """
        act = action.strip().lower()
        is_mutating = act not in ("read_file", "list_dir")
        target = SecurityGuardrails.resolve_safe_path(path, for_write_or_delete=is_mutating)

        if act == "create_folder":
            target.mkdir(parents=True, exist_ok=True)
            return {"exit_code": 0, "stdout": f"Created folder: {target}", "stderr": ""}

        if act == "delete":
            if not target.exists():
                return {"exit_code": 1, "stdout": "", "stderr": f"Path not found: {target}"}
            send2trash(str(target))
            return {
                "exit_code": 0,
                "stdout": f"Moved to Recycle Bin (send2trash): {target}",
                "stderr": "",
            }

        if act == "move":
            if not destination:
                return {"exit_code": 1, "stdout": "", "stderr": "Missing 'destination' argument for move."}
            if not target.exists():
                return {"exit_code": 1, "stdout": "", "stderr": f"Source path not found: {target}"}
            dest = SecurityGuardrails.resolve_safe_path(destination, for_write_or_delete=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            moved = shutil.move(str(target), str(dest))
            return {"exit_code": 0, "stdout": f"Moved {target} -> {moved}", "stderr": ""}

        if act == "write_file":
            target.parent.mkdir(parents=True, exist_ok=True)
            cleaned = cls._strip_code_fences(content) if target.suffix == ".py" else content
            target.write_text(cleaned, encoding="utf-8")
            return {
                "exit_code": 0,
                "stdout": f"Wrote {len(cleaned.encode('utf-8'))} bytes to {target}",
                "stderr": "",
                "file_path": str(target),
            }

        if act in ("read_file", "list_dir"):
            if not target.exists():
                return {"exit_code": 1, "stdout": "", "stderr": f"Path does not exist: {target}"}
            if target.is_dir():
                items = [p.name + ("/" if p.is_dir() else "") for p in sorted(target.iterdir())]
                listing = "\n".join(items[:150]) or "(empty directory)"
                return {"exit_code": 0, "stdout": f"Contents of {target}:\n{listing}", "stderr": ""}
            text = target.read_text(encoding="utf-8", errors="replace")
            return {"exit_code": 0, "stdout": text[:3000], "stderr": "", "file_path": str(target)}

        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Unsupported action '{action}'. Use create_folder, delete, move, write_file, read_file, list_dir.",
        }

    @classmethod
    def execute_bash(cls, command: str) -> Dict[str, Any]:
        """
        Runs a guardrailed shell command with timeout=30 and stdin=subprocess.DEVNULL.
        """
        SecurityGuardrails.validate_bash_command(command)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(WORKSPACE_DIR),
                timeout=SUBPROCESS_TIMEOUT,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            stdout_str = (proc.stdout or "")[:2000]
            stderr_lines = (proc.stderr or "").strip().splitlines()
            stderr_str = "\n".join(stderr_lines[-25:])
            return {
                "exit_code": proc.returncode,
                "stdout": stdout_str,
                "stderr": stderr_str,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"TimeoutExpired: Command exceeded {SUBPROCESS_TIMEOUT}s limit.",
            }

    @classmethod
    def open_app(cls, command_or_path: str) -> Dict[str, Any]:
        """
        Launches a desktop application or file and validates startup with psutil.
        """
        raw = command_or_path.strip()
        if not raw:
            return {"exit_code": 1, "stdout": "", "stderr": "command_or_path cannot be empty."}

        candidate_path = Path(os.path.expanduser(os.path.expandvars(raw)))
        if sys.platform == "win32" and candidate_path.exists() and candidate_path.suffix.lower() not in (".exe", ".bat", ".cmd"):
            os.startfile(str(candidate_path))  # type: ignore[attr-defined]
            return {"exit_code": 0, "stdout": f"Opened '{candidate_path}' via Windows Shell.", "stderr": ""}

        args = shlex.split(raw, posix=(sys.platform != "win32"))
        executable_name = Path(args[0]).stem.lower()
        script_hosts = {"cmd", "powershell", "pwsh", "python", "pythonw", "py", "wscript", "cscript", "bash", "sh", "node"}
        if executable_name in script_hosts and len(args) > 1:
            raise PermissionError("Launching command interpreters with arguments is blocked for safety.")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=(sys.platform != "win32"),
            )
            time.sleep(0.25)
            if psutil.pid_exists(proc.pid):
                ps_proc = psutil.Process(proc.pid)
                return {
                    "exit_code": 0,
                    "stdout": f"Launched '{ps_proc.name()}' (PID: {proc.pid})",
                    "stderr": "",
                }
            return {
                "exit_code": 0,
                "stdout": f"Dispatched application command '{raw}' (detached launcher exited).",
                "stderr": "",
            }
        except FileNotFoundError:
            if sys.platform == "win32":
                os.startfile(raw)  # type: ignore[attr-defined]
                return {"exit_code": 0, "stdout": f"Launched '{raw}' via os.startfile.", "stderr": ""}
            raise

    @classmethod
    def open_in_vscode(cls, path: str, line: int = 1, col: int = 1) -> Dict[str, Any]:
        """Opens a file in VS Code at line:col using `code -g`."""
        target = SecurityGuardrails.resolve_safe_path(path, for_write_or_delete=False)
        code_bin = (
            shutil.which("code")
            or shutil.which("code.cmd")
            or str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd")
        )
        if not Path(code_bin).exists() and not shutil.which(code_bin):
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "VS Code CLI ('code') not found on system PATH.",
            }

        target_spec = f"{target}:{max(1, int(line))}:{max(1, int(col))}"
        subprocess.Popen(
            [code_bin, "-g", target_spec],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"exit_code": 0, "stdout": f"Opened in VS Code: {target_spec}", "stderr": ""}

    @staticmethod
    def _strip_code_fences(code: str) -> str:
        cleaned = code.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines) + "\n"
        return cleaned

    @classmethod
    def verify_and_run(cls, file_path: str, code_content: str = "") -> Dict[str, Any]:
        """
        Writes `code_content` if provided, compiles via `py_compile`, and executes the Python
        script in an isolated subprocess with `timeout=30` and `stdin=subprocess.DEVNULL`.
        """
        target = SecurityGuardrails.resolve_safe_path(
            file_path,
            for_write_or_delete=bool(code_content.strip()),
        )

        if code_content.strip():
            target.parent.mkdir(parents=True, exist_ok=True)
            cleaned_code = cls._strip_code_fences(code_content)
            target.write_text(cleaned_code, encoding="utf-8")

        if not target.exists():
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"FileNotFoundError: Script not found at {target}",
                "file_path": str(target),
            }

        # 1. Static syntax compilation check
        compile_proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(target)],
            capture_output=True,
            text=True,
            cwd=str(target.parent),
            timeout=SUBPROCESS_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if compile_proc.returncode != 0:
            err = (compile_proc.stderr or compile_proc.stdout).strip()
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"SyntaxCompilationError:\n{err}",
                "file_path": str(target),
            }

        # 2. Subprocess execution
        try:
            run_proc = subprocess.run(
                [sys.executable, str(target)],
                capture_output=True,
                text=True,
                cwd=str(target.parent),
                timeout=SUBPROCESS_TIMEOUT,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            stdout_trimmed = (run_proc.stdout or "")[:2000]
            stderr_lines = (run_proc.stderr or "").strip().splitlines()
            stderr_trimmed = "\n".join(stderr_lines[-25:])
            return {
                "exit_code": run_proc.returncode,
                "stdout": stdout_trimmed,
                "stderr": stderr_trimmed,
                "file_path": str(target),
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"TimeoutExpired: Script '{target.name}' exceeded {SUBPROCESS_TIMEOUT}s.",
                "file_path": str(target),
            }

    @classmethod
    def download_and_open(
        cls,
        url: str,
        output_name: str = "",
        auto_open: bool = True,
    ) -> Dict[str, Any]:
        """
        Streams a file download to ~/Downloads (or opens a web URL if not a direct file)
        and launches it with the native OS handler.
        """
        safe_url = cls._validate_public_url(url)
        parsed = urllib.parse.urlsplit(safe_url)
        inferred_name = output_name.strip() or Path(urllib.parse.unquote(parsed.path)).name
        if not inferred_name or "." not in inferred_name:
            if auto_open:
                return cls.open_web_url(safe_url)
            inferred_name = inferred_name or "downloaded_file.html"

        result = cls.download_file(safe_url, inferred_name)
        if result.get("exit_code"):
            return result
        dest = Path(result["file_path"])
        msg = result["stdout"]
        if auto_open:
            if sys.platform == "win32":
                os.startfile(str(dest))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(dest)], stdin=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(dest)], stdin=subprocess.DEVNULL)
            msg += " and opened with system viewer."

        return {"exit_code": 0, "stdout": msg, "stderr": "", "file_path": str(dest)}

    @classmethod
    def _validate_public_url(cls, url: str) -> str:
        candidate = url.strip()
        if not candidate:
            raise ValueError("URL cannot be empty.")
        if "://" not in candidate:
            candidate = "https://" + candidate
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Only public HTTP and HTTPS URLs are supported.")
        if parsed.username or parsed.password:
            raise ValueError("URLs containing credentials are not allowed.")

        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            raise ValueError("Local and internal network addresses are not allowed.")

        try:
            addresses = {ipaddress.ip_address(hostname)}
        except ValueError:
            try:
                addresses = {
                    ipaddress.ip_address(info[4][0])
                    for info in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
                }
            except (OSError, ValueError) as exc:
                raise ValueError(f"Could not resolve public host '{hostname}'.") from exc

        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("Local, private, and reserved network addresses are not allowed.")
        return urllib.parse.urlunsplit(parsed)

    @classmethod
    def open_web_url(cls, url: str) -> Dict[str, Any]:
        safe_url = cls._validate_public_url(url)
        if not webbrowser.open(safe_url, new=2):
            return {"exit_code": 1, "stdout": "", "stderr": f"Could not open URL: {safe_url}"}
        return {"exit_code": 0, "stdout": f"Opened URL in the default browser: {safe_url}", "stderr": ""}

    @classmethod
    def fetch_webpage(cls, url: str) -> Dict[str, Any]:
        safe_url = cls._validate_public_url(url)
        response = requests.get(
            safe_url,
            headers={"User-Agent": cls.USER_AGENT},
            timeout=(5, 15),
            allow_redirects=False,
            stream=True,
        )
        with response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type and not (
                content_type.startswith("text/")
                or content_type in ("application/json", "application/xml", "application/xhtml+xml", "application/javascript")
            ):
                raise ValueError(f"Refusing non-text response type: {content_type}")

            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > 512_000:
                    raise ValueError("Web response exceeds the 512 KB limit.")

            encoding = response.encoding or "utf-8"
            text = bytes(body).decode(encoding, errors="replace")
            return {
                "exit_code": 0,
                "stdout": f"URL: {safe_url}\nContent-Type: {content_type or 'unknown'}\n{text[:12000]}",
                "stderr": "",
            }

    @classmethod
    def download_file(cls, url: str, output_name: str = "") -> Dict[str, Any]:
        safe_url = cls._validate_public_url(url)
        response = requests.get(
            safe_url,
            headers={"User-Agent": cls.USER_AGENT},
            timeout=(5, 30),
            allow_redirects=False,
            stream=True,
        )
        with response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 50_000_000:
                raise ValueError("Download exceeds the 50 MB limit.")

            inferred_name = Path(urllib.parse.unquote(urllib.parse.urlsplit(safe_url).path)).name
            filename = Path(output_name or inferred_name or "downloaded_file").name
            if filename in ("", ".", ".."):
                filename = "downloaded_file"

            destination = DOWNLOADS_DIR / filename
            stem, suffix = destination.stem, destination.suffix
            index = 1
            while destination.exists():
                destination = DOWNLOADS_DIR / f"{stem}_{index}{suffix}"
                index += 1

            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            temporary_path: Optional[str] = None
            total_bytes = 0
            try:
                with tempfile.NamedTemporaryFile(dir=DOWNLOADS_DIR, prefix=".ares-", suffix=".part", delete=False) as temp_file:
                    temporary_path = temp_file.name
                    for chunk in response.iter_content(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > 50_000_000:
                            raise ValueError("Download exceeds the 50 MB limit.")
                        temp_file.write(chunk)
                os.replace(temporary_path, destination)
            except Exception:
                if temporary_path and os.path.exists(temporary_path):
                    os.unlink(temporary_path)
                raise

            return {
                "exit_code": 0,
                "stdout": f"Downloaded {total_bytes} bytes to {destination}",
                "stderr": "",
                "file_path": str(destination),
            }

    @classmethod
    def set_system_setting(cls, setting: str, value: Any) -> Dict[str, Any]:
        normalized = setting.strip().lower()
        if normalized not in {"volume", "mute", "brightness"}:
            raise ValueError("Supported settings are volume, mute, and brightness.")

        if normalized == "brightness":
            percentage = float(value)
            if not 0 <= percentage <= 100:
                raise ValueError("Brightness must be between 0 and 100.")
            import screen_brightness_control

            screen_brightness_control.set_brightness(round(percentage))
            return {"exit_code": 0, "stdout": f"Brightness set to {round(percentage)}%.", "stderr": ""}

        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        speakers = AudioUtilities.GetSpeakers()
        endpoint = speakers.EndpointVolume
        if normalized == "mute":
            if not isinstance(value, bool):
                raise ValueError("Mute value must be true or false.")
            endpoint.SetMute(1 if value else 0, None)
            return {"exit_code": 0, "stdout": f"Mute {'enabled' if value else 'disabled'}.", "stderr": ""}

        percentage = float(value)
        if not 0 <= percentage <= 100:
            raise ValueError("Volume must be between 0 and 100.")
        endpoint.SetMasterVolumeLevelScalar(percentage / 100, None)
        return {"exit_code": 0, "stdout": f"Volume set to {round(percentage)}%.", "stderr": ""}

    @classmethod
    def open_settings_page(cls, section: str) -> Dict[str, Any]:
        settings_uris = {
            "display": "display",
            "sound": "sound",
            "bluetooth": "bluetooth",
            "network": "network-status",
            "apps": "appsfeatures",
            "personalization": "personalization",
            "time": "dateandtime",
            "privacy": "privacy",
        }
        normalized = section.strip().lower()
        if normalized not in settings_uris:
            raise ValueError(f"Settings section must be one of: {', '.join(sorted(settings_uris))}.")
        if sys.platform != "win32":
            raise RuntimeError("Opening a settings page is currently supported on Windows only.")
        os.startfile(f"ms-settings:{settings_uris[normalized]}")  # type: ignore[attr-defined]
        return {"exit_code": 0, "stdout": f"Opened Windows Settings: {normalized}.", "stderr": ""}

    @classmethod
    def inspect_window(cls, window_title_keyword: str) -> Dict[str, Any]:
        from ares.tools.os_desktop import DesktopManager

        result = DesktopManager.inspect_ui_tree(window_title_keyword)
        return {"exit_code": 0, "stdout": result, "stderr": ""}

    @classmethod
    def send_input(
        cls,
        action_type: str,
        target_name: str = "",
        value: str = "",
    ) -> Dict[str, Any]:
        from ares.tools.os_desktop import DesktopManager

        action = action_type.strip().lower()
        if action == "hotkey":
            allowed_hotkeys = {"ctrl+a", "ctrl+c", "ctrl+v", "ctrl+s", "ctrl+f", "ctrl+l", "ctrl+n", "ctrl+o", "ctrl+p", "tab", "shift+tab", "esc"}
            if value.strip().lower() not in allowed_hotkeys:
                raise ValueError("That hotkey is not allowlisted.")
        elif action == "type" and len(value) > 4000:
            raise ValueError("Typed input is limited to 4,000 characters per action.")
        elif action == "click_xy":
            raise ValueError("Coordinate clicks are disabled; use a named UI control.")
        elif action not in {"type", "click_control"}:
            raise ValueError("Supported desktop input actions are type, hotkey, and click_control.")
        if action == "click_control" and re.search(
            r"\b(send|delete|remove|purchase|buy|install|reset|format|submit|confirm)\b",
            target_name,
            re.IGNORECASE,
        ):
            raise ValueError("This control may cause an external or destructive action; confirmation is not available.")
        if action in {"hotkey", "type"} and not target_name.strip():
            raise ValueError("Specify the target window title so input cannot go to an unintended app.")

        result = DesktopManager.send_input(action, target_name=target_name, value=value)
        return {"exit_code": 0, "stdout": result, "stderr": ""}

    @classmethod
    def create_email_draft(cls, to: str, subject: str, body: str) -> Dict[str, Any]:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        draft_dir = SecurityGuardrails.resolve_safe_path(str(WORKSPACE_DIR / "email_drafts"))
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft_path = draft_dir / f"draft-{time.time_ns()}.eml"
        draft_path.write_bytes(message.as_bytes())
        return {
            "exit_code": 0,
            "stdout": f"Email draft saved locally; it was not sent: {draft_path}",
            "stderr": "",
            "file_path": str(draft_path),
        }

    @classmethod
    def dispatch(cls, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Routes a tool call to its handler and captures any guardrail or runtime exceptions."""
        handlers: Dict[str, Callable[..., Dict[str, Any]]] = {
            "manage_fs": cls.manage_fs,
            "execute_bash": cls.execute_bash,
            "open_app": cls.open_app,
            "open_in_vscode": cls.open_in_vscode,
            "verify_and_run": cls.verify_and_run,
            "download_and_open": cls.download_and_open,
            "open_web_url": cls.open_web_url,
            "fetch_webpage": cls.fetch_webpage,
            "download_file": cls.download_file,
            "set_system_setting": cls.set_system_setting,
            "open_settings_page": cls.open_settings_page,
            "inspect_window": cls.inspect_window,
            "send_input": cls.send_input,
            "create_email_draft": cls.create_email_draft,
        }
        if tool_name not in handlers:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Unknown tool '{tool_name}'. Valid tools: {list(handlers.keys()) + ['finish']}",
            }
        try:
            return handlers[tool_name](**args)
        except Exception as exc:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "file_path": args.get("file_path") or args.get("path"),
            }


# ============================================================================
# 5. PROMPTS & 16-STEP REACT ENGINE
# ============================================================================

SYSTEM_PROMPT = (
    "You are ARES, a local autonomous system agent running on Windows/Linux.\n"
    "You execute tasks using Thought, Action, and Observation cycles.\n\n"
    "TOOLS AVAILABLE:\n"
    "- manage_fs(action, path, destination, content): create/read/write within the workspace; delete is allowed only outside protected system paths.\n"
    "- execute_bash(command): run a bounded, non-destructive command; never use it to modify protected paths.\n"
    "- open_app(command_or_path): launch a desktop application, but never pass arguments to a command interpreter.\n"
    "- open_in_vscode(path, line): open a workspace file in VS Code.\n"
    "- verify_and_run(file_path): compile and execute a Python file in the workspace.\n"
    "- download_and_open(url, output_name, auto_open): download a public file (max 50 MB) or open a public URL.\n"
    "- open_web_url(url), fetch_webpage(url), download_file(url, output_name): use public HTTP/HTTPS URLs only.\n"
    "- set_system_setting(setting, value): set volume, mute, or brightness within 0-100.\n"
    "- open_settings_page(section): open an allowlisted Windows Settings page.\n"
    "- inspect_window(window_title_keyword), send_input(action_type, window_title, target_name, value): interact with a named desktop window only.\n"
    "- create_email_draft(to, subject, body): save a local draft; never send email.\n"
    "- finish(result): report final success/answer and exit.\n\n"
    "OUTPUT FORMAT (STRICT):\n"
    "Thought: <Concise reasoning about current state>\n"
    "Action:\n"
    "```json\n"
    "{\n"
    '  "tool": "tool_name",\n'
    '  "args": { "param": "value" }\n'
    "}\n"
    "```\n"
    "For greetings or conversation that require no system action, use finish with a concise reply.\n"
    "For multi-step requests, use one tool per step and continue until the requested steps are verified or the 16-step limit is reached.\n"
    "Never delete, overwrite, or execute files in system directories; never send email or click controls that submit, purchase, install, delete, or reset.\n"
    "Never invent tool results. If an operation is unsupported or unsafe, explain that using finish.\n"
    "Rules: Never predict observations. Output valid JSON only. Stop immediately after the JSON action block."
)

# ==============================================================================
# REACT ENGINE (With Escalation & Robust Parsing)
# ==============================================================================
class ReActEngine:
    def __init__(self, manager: Optional[ModelManager] = None):
        self.mgr = manager if manager is not None else ModelManager()
        self.executor = ToolExecutor()

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
        match2 = re.search(r"(\{.*\})", text, re.DOTALL)
        if match2:
            try:
                return json.loads(match2.group(1))
            except Exception:
                pass
        return None

    def run(self, user_query: str) -> Generator[Dict[str, Any], None, None]:
        if re.fullmatch(r"(?:hi|hello|hey)(?:\s+there)?[!.?]*", user_query.strip(), re.IGNORECASE):
            yield {"type": "finish", "result": "Hello! How can I help?"}
            return

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_query}
        ]
        active_model = MODEL_ORCHESTRATOR
        prev_actions = []

        for step in range(1, 17):
            # 1. Context compaction check (~3.5 chars per token estimate)
            approx_tokens = sum(len(m["content"]) for m in messages) // 3.5
            if approx_tokens > 3500:
                yield {"type": "status", "data": "Compacting conversation context..."}
                summary_prompt = "Summarize the key achievements and active state of this conversation briefly."
                summary = self.mgr.chat(messages + [{"role": "user", "content": summary_prompt}], model=MODEL_COMPACTOR)
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Original Goal: {user_query}\nState so far: {summary}"}
                ]

            yield {"type": "status", "data": f"Step {step}/16 | Model: {active_model}"}

            # 2. Model Inference
            raw_response = self.mgr.chat(messages, model=active_model)
            parsed_action = self._extract_json(raw_response)

            thought = raw_response.split("Action:")[0].replace("Thought:", "").strip()
            yield {"type": "thought", "data": thought}

            if not parsed_action or "tool" not in parsed_action:
                if "Action:" not in raw_response and "```" not in raw_response:
                    yield {"type": "finish", "result": raw_response.strip() or "Hello! How can I help?"}
                    return
                obs = "Error: Invalid Action formatting. You must provide a valid JSON action block."
                messages.append({"role": "assistant", "content": raw_response})
                messages.append({"role": "user", "content": f"Observation: {obs}"})
                continue

            tool = parsed_action.get("tool")
            args = parsed_action.get("args", {})
            if isinstance(args, str):
                string_arg_names = {
                    "execute_bash": "command",
                    "open_app": "command_or_path",
                }
                arg_name = string_arg_names.get(tool)
                args = {arg_name: args} if arg_name else {}
            elif not isinstance(args, dict):
                args = {}
            else:
                args = dict(args)

            if tool == "manage_fs" and "dest" in args and "destination" not in args:
                args["destination"] = args.pop("dest")
            if (
                tool == "manage_fs"
                and not args.get("action")
                and re.search(r"\b(create|make|new)\b", user_query, re.IGNORECASE)
                and re.search(r"\b(folder|directory)\b", user_query, re.IGNORECASE)
            ):
                args["action"] = "create_folder"
            if (
                tool == "manage_fs"
                and args.get("action") == "create_folder"
                and args.get("path") in ("", ".")
                and args.get("destination")
            ):
                args["path"] = args.pop("destination")
            if tool == "open_app" and "command_or_name" in args and "command_or_path" not in args:
                args["command_or_path"] = args.pop("command_or_name")
            if tool == "download_and_open" and "filename" in args and "output_name" not in args:
                args["output_name"] = args.pop("filename")

            yield {"type": "action", "tool": tool, "args": args}

            # 3. Completion Check
            if tool == "finish":
                yield {"type": "finish", "result": args.get("result", "Task completed.")}
                return

            # 4. Stuck Loop Detection
            action_sig = f"{tool}:{json.dumps(args, sort_keys=True)}"
            prev_actions.append(action_sig)
            if len(prev_actions) >= 2 and prev_actions[-1] == prev_actions[-2]:
                yield {"type": "status", "data": "Stuck loop detected. Escalating to DeepSeek-R1..."}
                active_model = MODEL_REASONER

            # 5. Tool Execution
            result = self.executor.dispatch(tool, args)
            is_error = result.get("exit_code", 1) != 0
            obs = result.get("stdout", "")
            if is_error:
                obs = f"stderr: {result.get('stderr') or obs}"

            yield {"type": "observation", "data": obs}

            # 6. Error Escalation
            if is_error and active_model != MODEL_DEBUGGER:
                yield {"type": "status", "data": "Execution error detected. Escalating to Qwen 3B Debugger..."}
                active_model = MODEL_DEBUGGER
            elif not is_error and active_model != MODEL_ORCHESTRATOR:
                active_model = MODEL_ORCHESTRATOR

            messages.append({"role": "assistant", "content": raw_response})
            messages.append({"role": "user", "content": f"Observation: {obs}"})

        yield {"type": "finish", "result": "Reached maximum 16 execution steps limit."}