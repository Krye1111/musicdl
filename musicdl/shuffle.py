"""Flatten + randomly renumber the audio files inside a folder.

Given a target directory, this walks every sub-folder, gathers all audio
files, moves them up into the target directory, and renames them to a
*shuffled* sequential numbering (``001.mp3``, ``002.mp3``, …). The now-empty
sub-folders are removed afterwards.

Like the rest of ``musicdl`` this module is UI-agnostic: it reports progress
through a plain ``on_log`` callback so it can be driven from the GUI, a CLI,
or tests.

The operation is intentionally collision-safe: files are first moved to
unique temporary names, then renamed to their final numbers, so two files
called ``01 - Intro.mp3`` living in different album folders never clobber
each other.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Audio extensions we treat as "songs". Lower-case, dot-prefixed.
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus",
    ".wma", ".alac", ".aiff", ".aif",
}

LogCallback = Callable[[str, str], None]


@dataclass
class ShuffleResult:
    """Summary of a shuffle run."""

    moved: int = 0            # files renumbered into the target folder
    folders_removed: int = 0  # emptied sub-folders cleaned up
    skipped: int = 0          # files left untouched (e.g. rename failures)


def _noop_log(_message: str, _level: str = "info") -> None:
    pass


def collect_audio_files(target: Path) -> list[Path]:
    """Return every audio file at or below *target* (recursively)."""
    files: list[Path] = []
    for path in target.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(path)
    return files


def shuffle_folder(
    target: str | Path,
    *,
    seed: int | None = None,
    remove_empty_dirs: bool = True,
    on_log: LogCallback | None = None,
) -> ShuffleResult:
    """Flatten *target* and randomly renumber the audio files inside it.

    Parameters
    ----------
    target:
        The folder to shuffle. Audio files anywhere beneath it are pulled up
        into this folder and renamed ``NNN.<ext>`` in random order.
    seed:
        Optional RNG seed for reproducible shuffles (handy for tests).
    remove_empty_dirs:
        When True, sub-folders left empty after the move are deleted.
    on_log:
        ``(message, level)`` callback; ``level`` is one of
        ``info | success | warn | error``.

    Returns
    -------
    ShuffleResult
        Counts describing what happened.
    """
    log = on_log or _noop_log
    root = Path(target).expanduser()

    if not root.is_dir():
        log(f"Not a folder: {root}", "error")
        return ShuffleResult()

    files = collect_audio_files(root)
    if not files:
        log(f"No audio files found under {root}.", "warn")
        return ShuffleResult()

    rng = random.Random(seed)
    rng.shuffle(files)

    # Zero-pad wide enough for the whole set (min 3 digits: 001, 002, …).
    width = max(3, len(str(len(files))))
    result = ShuffleResult()

    log(f"Found {len(files)} audio file(s). Shuffling…", "info")

    # --- Phase 1: move everything to unique temp names in the root. --------
    # A temp name avoids collisions between same-named tracks from different
    # sub-folders and avoids clobbering a target number that already exists.
    staged: list[Path] = []
    for src in files:
        tmp = root / f".musicdl-shuffle-{uuid.uuid4().hex}{src.suffix.lower()}"
        try:
            src.replace(tmp)
            staged.append(tmp)
        except OSError as exc:
            result.skipped += 1
            log(f"Could not move {src.name}: {exc}", "warn")

    # --- Phase 2: rename the staged files to their final shuffled numbers. -
    for index, tmp in enumerate(staged, start=1):
        final = root / f"{index:0{width}d}{tmp.suffix}"
        # Extremely unlikely, but guard against a pre-existing NNN file that
        # was not part of our set (e.g. it lacked an audio extension change).
        if final.exists() and final != tmp:
            final = root / f"{index:0{width}d}-{uuid.uuid4().hex[:4]}{tmp.suffix}"
        try:
            tmp.replace(final)
            result.moved += 1
        except OSError as exc:
            result.skipped += 1
            log(f"Could not rename to {final.name}: {exc}", "warn")

    # --- Phase 3: clean up emptied sub-folders. ----------------------------
    if remove_empty_dirs:
        result.folders_removed = _remove_empty_dirs(root)

    log(
        f"Shuffle complete — {result.moved} file(s) renumbered, "
        f"{result.folders_removed} empty folder(s) removed"
        + (f", {result.skipped} skipped" if result.skipped else "")
        + ".",
        "success" if not result.skipped else "warn",
    )
    return result


def _remove_empty_dirs(root: Path) -> int:
    """Recursively remove empty directories *below* root. Root itself stays."""
    removed = 0
    # Walk deepest-first so parents become empty after their children go.
    dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except OSError:
            # Non-empty (hidden files, permissions) — leave it be.
            pass
    return removed
