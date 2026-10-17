"""Filesystem / string helpers shared across musicdl."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# Characters Windows (and, conservatively, all OSes) forbid in file names.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Trailing dots/spaces are illegal as a Windows path component.
_TRAILING = re.compile(r"[ .]+$")


def sanitize_filename(name: str, fallback: str = "Unknown") -> str:
    """Return *name* made safe for use as a single path component.

    Strips illegal characters, collapses whitespace, and guards against
    empty / reserved results so we never emit an unusable path.
    """
    if not name:
        return fallback
    cleaned = _INVALID_CHARS.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _TRAILING.sub("", cleaned)
    # Windows reserved device names.
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not cleaned or cleaned.upper() in reserved:
        return fallback
    # Cap length to stay well under path limits.
    return cleaned[:150]


def album_dir_name(year: str | None, album: str) -> str:
    """Build the '[Year] - [Album]' directory component."""
    album_safe = sanitize_filename(album, "Unknown Album")
    if year:
        return f"{year} - {album_safe}"
    return album_safe


def track_file_name(track_no: int | None, title: str, ext: str = "mp3") -> str:
    """Build the '[NN] - [Title].ext' file component."""
    title_safe = sanitize_filename(title, "Unknown Track")
    if track_no:
        return f"{track_no:02d} - {title_safe}.{ext}"
    return f"{title_safe}.{ext}"


def ffmpeg_available(explicit_location: str | None = None) -> bool:
    """Return True if an ffmpeg executable can be located."""
    if explicit_location:
        candidate = Path(explicit_location)
        if candidate.is_dir():
            candidate = candidate / "ffmpeg"
        return shutil.which(str(candidate)) is not None or candidate.exists()
    return shutil.which("ffmpeg") is not None


def human_size(num_bytes: float | None) -> str:
    """Format a byte count as a compact human-readable string."""
    if not num_bytes:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"
