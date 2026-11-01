"""
Module E: CPU-based Tool-RAG using fastembed (sentence-transformers/all-MiniLM-L6-v2)
and universal CodeAct fallbacks (execute_bash, execute_python).
"""

from dataclasses import dataclass
import logging
import subprocess
from typing import Any, Callable, Dict, List, Optional
import uuid

import numpy as np

from ares.config import (
    EMBEDDING_MODEL_NAME,
    MAX_STDERR_LINES,
    MAX_STDOUT_CHARS,
    SUBPROCESS_TIMEOUT_SECONDS,
    WORKSPACE_DIR,
)
from ares.tools.browser import BrowserToolkit
from ares.tools.coder import CodeDoctor
from ares.tools.os_desktop import DesktopManager, FileSystemOps

logger = logging.getLogger("ares.tools.registry")

try:
    from fastembed import TextEmbedding

    HAS_FASTEMBED = True
except Exception:
    HAS_FASTEMBED = False


@dataclass
class RegisteredTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Callable[..., Any]
    always_include: bool = False
    embedding: Optional[np.ndarray] = None

    def to_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """
    Maintains all system tools, computes CPU-only vector embeddings via fastembed,
    retrieves top-k domain tools per user query, and provides universal CodeAct execution.
    """

    def __init__(self) -> None:
        self.tools: Dict[str, RegisteredTool] = {}
        self.embedder: Optional[ Any] = None
        self._init_cpu_embedder()
        self._register_builtin_tools()
        self._precompute_tool_embeddings()

    def _init_cpu_embedder(self) -> None:
        """Initializes fastembed strictly on CPUExecutionProvider."""
        if not HAS_FASTEMBED:
            logger.warning("fastembed not installed; falling back to CPU lexical cosine matcher.")
            return
        try:
            self.embedder = TextEmbedding(
                model_name=EMBEDDING_MODEL_NAME,
                providers=["CPUExecutionProvider"],
                threads=4,
            )
            logger.info("Initialized CPU Tool-RAG embedder: %s", EMBEDDING_MODEL_NAME)
        except Exception as exc:
            logger.warning("Could not initialize fastembed model (%s); using CPU lexical fallback.", exc)
            self.embedder = None

    def _embed_texts(self, texts: List[str]) -> np.ndarray:
        """Generates L2-normalized embeddings strictly on CPU."""
        if self.embedder is not None:
            vectors = list(self.embedder.embed(texts))
            arr = np.asarray(vectors, dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-10
            return arr / norms

        # Deterministic offline character n-gram hash embedding on CPU if ONNX cache is absent
        dim = 256
        mat = np.zeros((len(texts), dim), dtype=np.float32)
        for row_idx, text in enumerate(texts):
            tokens = text.lower().split()
            for tok in tokens:
                h = hash(tok) % dim
                mat[row_idx, h] += 1.0
        norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
        return mat / norms

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable[..., Any],
        always_include: bool = False,
    ) -> None:
        self.tools[name] = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            func=func,
            always_include=always_include,
        )

    def _register_builtin_tools(self) -> None:
        """Registers all domain tools and universal CodeAct fallback tools."""
        self.register(
            name="manage_fs",
            description="Create folders, delete files/folders, move paths, write text files, or read files/directories using pathlib.",
            parameters={
                "action": "str (create_folder | delete | move | write_file | read_file)",
                "path": "str (target file or folder path)",
                "destination": "str (optional destination path for move)",
                "content": "str (optional text content for write_file)",
            },
            func=FileSystemOps.manage_fs,
        )

        self.register(
            name="launch_app",
            description="Launch a desktop application or background executable and validate its PID with psutil.",
            parameters={"command_or_path": "str (executable command or path)"},
            func=DesktopManager.launch_app,
        )

        self.register(
            name="inspect_ui_tree",
            description="Inspect a desktop window accessibility UI tree for interactive controls (Button, Edit, MenuItem, CheckBox).",
            parameters={"window_title_keyword": "str (substring of window title)"},
            func=DesktopManager.inspect_ui_tree,
        )

        self.register(
            name="send_input",
            description="Send keyboard hotkeys, type text, click screen coordinates, or click named accessibility controls in a window.",
            parameters={
                "action_type": "str (hotkey | type | click_control | click_xy)",
                "target_name": "str (optional UI control name)",
                "value": "str (hotkey e.g. 'ctrl+s', text to type, window title, or 'x,y')",
            },
            func=DesktopManager.send_input,
        )

        self.register(
            name="open_in_vscode",
            description="Open a source file in VS Code editor at a specific line and column using code -g.",
            parameters={
                "path": "str (file path)",
                "line": "int (optional 1-based line number)",
                "col": "int (optional 1-based column number)",
            },
            func=CodeDoctor.open_in_vscode,
        )

        self.register(
            name="direct_write_code",
            description="Write source code directly to a file on disk for instant hot-reloading and execution.",
            parameters={
                "file_path": "str (destination script path)",
                "code_content": "str (complete source code)",
            },
            func=CodeDoctor.direct_write_code,
        )

        self.register(
            name="verify_and_run",
            description="Compile a Python file with py_compile for syntax errors and run it in an isolated subprocess.",
            parameters={
                "file_path": "str (path to Python script)",
                "timeout": "int (optional timeout in seconds, default 25)",
            },
            func=CodeDoctor.verify_and_run,
        )

        self.register(
            name="download_file",
            description="Download a file from an HTTP/HTTPS URL directly into ~/Downloads via streaming.",
            parameters={
                "url": "str (direct download URL)",
                "output_name": "str (optional filename in ~/Downloads)",
            },
            func=BrowserToolkit.download_file,
        )

        self.register(
            name="open_system_file",
            description="Open any downloaded file, PDF, image, or document with its default operating system viewer.",
            parameters={"file_path": "str (path to file)"},
            func=BrowserToolkit.open_system_file,
        )

        self.register(
            name="open_web_url",
            description="Open a web URL in the default desktop web browser.",
            parameters={"url": "str (URL to open)"},
            func=BrowserToolkit.open_web_url,
        )

        # Universal CodeAct Fallback Tools (Always Exposed)
        self.register(
            name="execute_bash",
            description="Universal fallback: Execute a shell/terminal command and capture stdout/stderr.",
            parameters={"command": "str (shell command to run)"},
            func=self.execute_bash,
            always_include=True,
        )

        self.register(
            name="execute_python",
            description="Universal fallback: Synthesize and run custom Python code on the fly for any unregistered task.",
            parameters={"code": "str (complete Python code to execute)"},
            func=self.execute_python,
            always_include=True,
        )

        self.register(
            name="finish",
            description="Conclude the task and return the final verified answer to the user.",
            parameters={"final_answer": "str (comprehensive final response)"},
            func=lambda final_answer="Task complete.": final_answer,
            always_include=True,
        )

    def _precompute_tool_embeddings(self) -> None:
        selectable = [t for t in self.tools.values() if not t.always_include]
        if not selectable:
            return
        corpus = [f"{t.name}: {t.description}" for t in selectable]
        embeddings = self._embed_texts(corpus)
        for tool_obj, vec in zip(selectable, embeddings):
            tool_obj.embedding = vec

    def get_top_tools(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Computes cosine similarity between the user prompt and registered tool descriptions
        on the CPU. Returns the top_k domain tools PLUS the universal fallback tools
        (execute_bash, execute_python, finish) to keep prompt size minimal.
        """
        selectable = [t for t in self.tools.values() if not t.always_include and t.embedding is not None]
        query_vec = self._embed_texts([query])[0]

        scored: List[tuple[float, RegisteredTool]] = []
        for tool_obj in selectable:
            score = float(np.dot(query_vec, tool_obj.embedding))  # type: ignore[arg-type]
            scored.append((score, tool_obj))

        scored.sort(key=lambda item: item[0], reverse=True)
        top_domain_tools = [item[1].to_schema() for item in scored[:top_k]]
        universal_tools = [t.to_schema() for t in self.tools.values() if t.always_include]
        return top_domain_tools + universal_tools

    @staticmethod
    def execute_bash(command: str) -> Dict[str, Any]:
        """Executes an arbitrary terminal command in WORKSPACE_DIR with timeout protection."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(WORKSPACE_DIR),
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
            stdout_trimmed = (proc.stdout or "")[:MAX_STDOUT_CHARS]
            stderr_lines = (proc.stderr or "").strip().splitlines()
            stderr_trimmed = "\n".join(stderr_lines[-MAX_STDERR_LINES:])
            return {
                "exit_code": proc.returncode,
                "stdout": stdout_trimmed,
                "stderr": stderr_trimmed,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"TimeoutExpired: Command exceeded {SUBPROCESS_TIMEOUT_SECONDS}s.",
            }
        except Exception as exc:
            return {"exit_code": 1, "stdout": "", "stderr": str(exc)}

    @staticmethod
    def execute_python(code: str) -> Dict[str, Any]:
        """Writes ad-hoc Python code to a workspace scratch file and runs CodeDoctor.verify_and_run."""
        scratch_name = f"_codeact_{uuid.uuid4().hex[:8]}.py"
        scratch_path = WORKSPACE_DIR / scratch_name
        CodeDoctor.direct_write_code(str(scratch_path), code)
        return CodeDoctor.verify_and_run(str(scratch_path), timeout=SUBPROCESS_TIMEOUT_SECONDS)

    def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Invokes a registered tool by name and normalizes its output to
        {"exit_code": int, "stdout": str, "stderr": str, "file_path": Optional[str]}.
        """
        if tool_name not in self.tools:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"KeyError: Unknown tool '{tool_name}'. Available: {list(self.tools.keys())}",
            }

        tool_obj = self.tools[tool_name]
        try:
            result = tool_obj.func(**args)
            if isinstance(result, dict) and "exit_code" in result:
                return result
            return {
                "exit_code": 0,
                "stdout": str(result),
                "stderr": "",
                "file_path": args.get("file_path") or args.get("path"),
            }
        except Exception as exc:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "file_path": args.get("file_path") or args.get("path"),
            }