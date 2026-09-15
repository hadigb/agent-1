"""Open the native folder chooser."""
from __future__ import annotations

import platform
import subprocess
from typing import Optional


def pick_folder(prompt: str = "پوشه پروژه جاوا را انتخاب کنید") -> Optional[str]:
    """Return an absolute folder path, or None if the user cancelled / picker unavailable."""
    system = platform.system()
    if system == "Darwin":
        return _macos(prompt)
    if system == "Windows":
        return _windows(prompt)
    return _linux(prompt)


def _normalize(path: str) -> Optional[str]:
    path = (path or "").strip()
    if not path:
        return None
    if len(path) > 1:
        path = path.rstrip("/\\")
    return path


def _macos(prompt: str) -> Optional[str]:
    script = f'''
try
    tell me to activate
    POSIX path of (choose folder with prompt "{_escape_as(prompt)}")
on error
    ""
end try
'''
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _normalize(out.stdout or "")


def _windows(prompt: str) -> Optional[str]:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        f"$d.Description = '{prompt.replace(chr(39), chr(39)+chr(39))}'; "
        "$d.ShowNewFolderButton = $false; "
        "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-STA", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _normalize(out.stdout or "")


def _linux(prompt: str) -> Optional[str]:
    for cmd in (
        ["zenity", "--file-selection", "--directory", f"--title={prompt}"],
        ["kdialog", "--getexistingdirectory", ".", prompt],
    ):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            continue
        path = _normalize(out.stdout or "")
        if path:
            return path
    return None


def _escape_as(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
