"""
System paths, Ollama endpoints, model matrix identifiers, and hardware budgets.
"""

import os
from pathlib import Path
from typing import List

# Force CPU-only execution for underlying CTranslate2 / ONNX / PyTorch libraries
# (Ollama runs as an independent daemon with its own GPU context)
os.environ.setdefault("ORT_DISABLE_ALL_OPTIMIZATION", "0")
os.environ.setdefault("OMP_NUM_THREADS", "4")

# Base Directories
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
WORKSPACE_DIR: Path = Path.home() / "ares_workspace"
DOWNLOADS_DIR: Path = Path.home() / "Downloads"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Ollama Daemon Configuration
OLLAMA_BASE_URL: str = os.environ.get("ARES_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_GENERATE_ENDPOINT: str = f"{OLLAMA_BASE_URL}/api/generate"
OLLAMA_CHAT_ENDPOINT: str = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_PS_ENDPOINT: str = f"{OLLAMA_BASE_URL}/api/ps"

# Sequential Model Matrix (4GB VRAM Ceiling)
MODEL_ORCHESTRATOR: str = "qwen2.5-coder:1.5b"
MODEL_DEBUGGER: str = "qwen2.5-coder:3b"
MODEL_REASONER: str = "deepseek-r1:1.5b"
MODEL_COMPACTOR: str = "qwen2.5:0.5b"

DEFAULT_KEEP_ALIVE: str = "5m"
EVICT_KEEP_ALIVE: int = 0
VRAM_SWAP_SETTLE_SECONDS: float = 0.20

# Determinism & Context Budgets
DEFAULT_NUM_CTX: int = 4096
COMPACTION_TOKEN_THRESHOLD: int = 3500
DEFAULT_TEMPERATURE: float = 0.0
MAX_REACT_STEPS: int = 16

# Strict Stop Sequences (ChatML & ReAct Observation Boundaries)
STOP_TOKENS: List[str] = [
    "Observation:",
    "```\nObservation",
    "<|im_end|>",
    "<|endoftext|>",
]

# Execution & Output Constraints
SUBPROCESS_TIMEOUT_SECONDS: int = 25
MAX_STDOUT_CHARS: int = 2000
MAX_STDERR_LINES: int = 25
UI_TREE_MAX_TOKENS: int = 250

# CPU Embedding & Voice Configuration
EMBEDDING_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
WHISPER_MODEL_SIZE: str = "base.en"
WHISPER_COMPUTE_TYPE: str = "int8"
AUDIO_SAMPLE_RATE: int = 16000
AUDIO_SILENCE_DURATION_S: float = 1.2
AUDIO_MAX_RECORD_S: float = 15.0