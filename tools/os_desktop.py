"""Compatibility wrapper for the desktop automation module.

The project originally used the filename `os_dekstop.py`, but import statements
reference `os_desktop.py`. Re-export the real implementation here so both names
work consistently.
"""

from .os_dekstop import *  # noqa: F401,F403
