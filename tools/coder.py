"""
Module C: VS Code CLI (-g) integration, sub-10ms direct disk code writing,
static py_compile verification, and isolated subprocess execution.
"""

import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ares.config import (
    MAX_STDERR_LINES,
    MAX_STDOUT_CHARS,
    SUBPROCESS_TIMEOUT_SECONDS,
    WORKSPACE_DIR,
)


class CodeDoctor:
    """
    Handles direct file writing, VS Code line/column navigation, static compilation,
    and isolated subprocess execution with truncated output capture.
    """

    @staticmethod
    def _resolve_path(file_path: str) -> Path:
        expanded = os.path.expanduser(os.path.expandvars(file_path))
        p = Path(expanded)
        if not p.is_absolute():
            p = (WORKSPACE_DIR / p).resolve()
        return p

    @classmethod
    def open_in_vscode(
        cls,
        path: str,
        line: Optional[int] = None,
        col: Optional[int] = None,
    ) -> str:
        """
        Opens a file in VS Code at an exact line and column using `code -g <path>:<line>:<col>`.
        Validates the existence of the `code` executable on PATH.
        """
        code_bin = shutil.which("code") or shutil.which("code.cmd") or shutil.which("code-insiders")
        if not code_bin:
            raise FileNotFoundError(
                "VS Code CLI executable ('code') was not found on system PATH."
            )

        target = cls._resolve_path(path)
        location_spec = str(target)
        if line is not None:
            location_spec += f":{max(1, int(line))}"
            if col is not None:
                location_spec += f":{max(1, int(col))}"

        subprocess.Popen(
            [code_bin, "-g", location_spec],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return f"Opened in VS Code: {location_spec}"

    @classmethod
    def direct_write_code(cls, file_path: str, code_content: str) -> str:
        """
        Writes code directly to disk so VS Code's native file watcher hot-reloads it in <10ms.
        """
        target = cls._resolve_path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Strip accidental markdown code fences if the LLM wrapped the payload
        cleaned = code_content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines) + "\n"

        target.write_text(cleaned, encoding="utf-8")
        return f"Wrote {len(cleaned.encode('utf-8'))} bytes to {target}"

    @classmethod
    def verify_and_run(
        cls,
        file_path: str,
        timeout: int = SUBPROCESS_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """
        1. Static syntax check via `python -m py_compile <file_path>`.
           If syntax fails, immediately returns exit_code=1 with compiler error.
        2. Subprocess execution in an isolated environment, capturing stdout (<=2,000 chars)
           and stderr (final 25 lines of traceback).
        """
        target = cls._resolve_path(file_path)
        if not target.exists():
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"FileNotFoundError: Script does not exist at {target}",
                "file_path": str(target),
            }

        # Step 1: Static syntax compilation check
        compile_proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(target)],
            capture_output=True,
            text=True,
            cwd=str(target.parent),
            timeout=10,
            check=False,
        )
        if compile_proc.returncode != 0:
            err_msg = (compile_proc.stderr or compile_proc.stdout).strip()
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"SyntaxCompilationError:\n{err_msg}",
                "file_path": str(target),
            }

        # Step 2: Isolated subprocess execution
        try:
            run_proc = subprocess.run(
                [sys.executable, str(target)],
                capture_output=True,
                text=True,
                cwd=str(target.parent),
                timeout=int(timeout),
                check=False,
            )
            stdout_trimmed = (run_proc.stdout or "")[:MAX_STDOUT_CHARS]
            stderr_lines = (run_proc.stderr or "").strip().splitlines()
            stderr_trimmed = "\n".join(stderr_lines[-MAX_STDERR_LINES:])

            return {
                "exit_code": run_proc.returncode,
                "stdout": stdout_trimmed,
                "stderr": stderr_trimmed,
                "file_path": str(target),
            }
        except subprocess.TimeoutExpired as exc:
            partial_out = (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))[:MAX_STDOUT_CHARS]
            return {
                "exit_code": 124,
                "stdout": partial_out,
                "stderr": f"TimeoutExpired: Script '{target.name}' exceeded {timeout}s execution limit.",
                "file_path": str(target),
            }
        except Exception as exc:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"SubprocessExecutionError: {exc}",
                "file_path": str(target),
            }

    @classmethod
    def read_numbered_source(cls, file_path: str) -> str:
        """Reads source code with 1-indexed line numbers for the 3B debugger model."""
        target = cls._resolve_path(file_path)
        if not target.exists():
            return ""
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(f"{idx + 1:4d} | {line}" for idx, line in enumerate(lines))