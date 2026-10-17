"""Central configuration for musicdl.

Everything tunable lives here so the rest of the code stays declarative.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# --- MusicBrainz identification (required by their API etiquette) -----------
# MusicBrainz asks every client to identify itself with a meaningful UA string.
MB_APP_NAME = "musicdl"
MB_APP_VERSION = "1.0.0"
MB_CONTACT = "https://github.com/yourname/musicdl"  # change to your own contact

# MusicBrainz enforces ~1 request/second. We stay polite by default.
MB_RATE_LIMIT_SECONDS = 1.1

# Cover Art Archive base URL (fed by MusicBrainz release-group MBIDs).
COVER_ART_BASE = "https://coverartarchive.org"


# --- Audio / download defaults ---------------------------------------------
AUDIO_BITRATE = "320"          # kbps, target MP3 bitrate
AUDIO_FORMAT = "mp3"
NETWORK_TIMEOUT = 30           # seconds, for yt-dlp socket + HTTP requests
DOWNLOAD_RETRIES = 3           # per-track retry attempts on transient failures
MAX_CONCURRENT_DOWNLOADS = 3   # worker threads used by the download pool


def _default_download_root() -> Path:
    """Return a sensible per-OS default download directory."""
    # Respect an explicit override first.
    env = os.environ.get("MUSICDL_DOWNLOAD_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Music" / "musicdl-Downloads"


@dataclass
class AppConfig:
    """Mutable runtime configuration, surfaced in the UI where useful."""

    download_root: Path = field(default_factory=_default_download_root)
    bitrate: str = AUDIO_BITRATE
    max_workers: int = MAX_CONCURRENT_DOWNLOADS
    # Path to the ffmpeg binary. None => let yt-dlp find it on PATH.
    ffmpeg_location: str | None = None

    def ensure_dirs(self) -> None:
        """Create the download root if it does not yet exist."""
        self.download_root.mkdir(parents=True, exist_ok=True)
