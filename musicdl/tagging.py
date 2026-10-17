"""ID3 metadata embedding via mutagen.

Kept deliberately small and defensive: tagging failures should never abort a
download that otherwise succeeded, so callers can treat these as best-effort.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TDRC,
    TIT2,
    TPE1,
    TRCK,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3


def embed_metadata(
    mp3_path: str | Path,
    *,
    title: str,
    artist: str,
    album: str,
    year: str | None = None,
    track_number: int | None = None,
    total_tracks: int | None = None,
    cover_art: bytes | None = None,
) -> None:
    """Write ID3v2 tags (and optional embedded cover art) onto *mp3_path*.

    Raises no domain-specific error; on failure it re-raises the underlying
    mutagen exception so the caller can log it without crashing the pipeline.
    """
    mp3_path = Path(mp3_path)

    # Ensure the file carries an ID3 header we can attach frames to.
    try:
        audio = MP3(mp3_path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(mp3_path)
        audio.add_tags()

    tags = audio.tags
    tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    tags.setall("TALB", [TALB(encoding=3, text=album)])

    if year:
        tags.setall("TDRC", [TDRC(encoding=3, text=str(year))])

    if track_number:
        trck = str(track_number)
        if total_tracks:
            trck = f"{track_number}/{total_tracks}"
        tags.setall("TRCK", [TRCK(encoding=3, text=trck)])

    if cover_art:
        tags.setall(
            "APIC",
            [APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,          # 3 = front cover
                desc="Cover",
                data=cover_art,
            )],
        )

    audio.save(v2_version=3)
