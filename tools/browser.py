"""
Module D: Headless streaming file downloads to ~/Downloads and native OS file/URL launchers.
"""

import os
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from ares.config import DOWNLOADS_DIR, WORKSPACE_DIR


class BrowserToolkit:
    """
    Provides zero-VRAM streaming downloads and native cross-platform file/URL openers.
    """

    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 ARES/1.0"
    )

    @classmethod
    def download_file(cls, url: str, output_name: str = "") -> str:
        """
        Streams a remote file directly to ~/Downloads via urllib.request without launching
        a heavy browser instance.
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme '{parsed.scheme}'. Only http/https allowed.")

        if not output_name.strip():
            inferred = Path(urllib.parse.unquote(parsed.path)).name
            output_name = inferred if inferred else "downloaded_artifact.bin"

        dest_path = (DOWNLOADS_DIR / Path(output_name).name).resolve()
        req = urllib.request.Request(url, headers={"User-Agent": cls.USER_AGENT})

        total_bytes = 0
        chunk_size = 64 * 1024
        with urllib.request.urlopen(req, timeout=60) as response, open(dest_path, "wb") as out_file:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                total_bytes += len(chunk)

        return f"Downloaded {total_bytes} bytes to {dest_path}"

    @staticmethod
    def open_system_file(file_path: str) -> str:
        """
        Cross-platform launcher using os.startfile (Windows), open (macOS), or xdg-open (Linux)
        to open any file, PDF, or media with its default system viewer.
        """
        expanded = os.path.expanduser(os.path.expandvars(file_path))
        target = Path(expanded)
        if not target.is_absolute():
            # Check Downloads and Workspace if relative path provided
            if (WORKSPACE_DIR / target).exists():
                target = (WORKSPACE_DIR / target).resolve()
            elif (DOWNLOADS_DIR / target).exists():
                target = (DOWNLOADS_DIR / target).resolve()
            else:
                target = target.resolve()

        if not target.exists():
            raise FileNotFoundError(f"Cannot open non-existent file: {target}")

        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["xdg-open", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        return f"Opened '{target}' with default system application."

    @staticmethod
    def open_web_url(url: str) -> str:
        """Opens a web URL in the system's default browser."""
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme:
            url = "https://" + url
        success = webbrowser.open(url, new=2)
        if not success:
            raise RuntimeError(f"System webbrowser failed to open URL: {url}")
        return f"Opened URL in default browser: {url}"