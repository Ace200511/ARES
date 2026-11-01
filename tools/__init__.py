"""OS Desktop, CodeDoctor, Browser, and CPU Tool-RAG registry."""

from ares.tools.browser import BrowserToolkit
from ares.tools.coder import CodeDoctor
from ares.tools.os_desktop import DesktopManager, FileSystemOps
from ares.tools.registry import ToolRegistry

__all__ = ["BrowserToolkit", "CodeDoctor", "DesktopManager", "FileSystemOps", "ToolRegistry"]