"""Compatibility package shim for the ARES workspace.

The project root is intentionally a flat Python workspace, but several modules
import it as a package (for example: `from ares.config import ...`). This file
makes the root directory resolve as the `ares` package without moving the
existing project structure.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
__path__ = [str(_ROOT)] + list(__path__)

__version__ = "1.0.0"
__author__ = "ARES Systems Architecture"
