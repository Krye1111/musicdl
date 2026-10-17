"""Download & audio-processing engine (yt-dlp + FFmpeg -> MP3 320k).

The engine is UI-agnostic: it emits progress through plain callbacks so it can
be driven by CustomTkinter, a CLI, or tests. All heavy work runs on a thread
pool, leaving the caller's (UI) thread free.

    LEGAL / TOS NOTE
    ----------------
    This engine locates audio by searching a public streaming source with
    yt-dlp. Downloading copyrighted material without permission may violate
    that service's Terms of Service and applicable copyright law. Use only for
    content you own, have licensed, or that is public domain / freely licensed.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import yt_dlp

from . import config, tagging
from .metadata import Album, Artist, Track
from .utils import album_dir_name, track_file_name


class JobState(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    TAGGING = "tagging"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class TrackJob:
    """One unit of work: fetch + convert + tag a single track."""

    artist: str
    album: Album
    track: Track
    dest_path: Path
    cover_art: bytes | None = None
    state: JobState = JobState.PENDING
    progress: float = 0.0          # 0.0 - 1.0
    error: str | None = None
    _id: str = field(default_factory=lambda: "")

    @property
    def id(self) -> str:
        return self._id or f"{self.album.title}::{self.track.number}"


# Callback signatures (all optional):
#   on_log(message: str, level: str)
#   on_job_update(job: TrackJob)
#   on_overall(done: int, total: int)
LogCallback = Callable[[str, str], None]
JobCallback = Callable[[TrackJob], None]
OverallCallback = Callable[[int, int], None]


class DownloadEngine:
    """Coordinates a batch of TrackJobs across a bounded thread pool."""

    def __init__(
        self,
        cfg: config.AppConfig,
        *,
        on_log: LogCallback | None = None,
        on_job_update: JobCallback | None = None,
        on_overall: OverallCallback | None = None,
    ) -> None:
        self.cfg = cfg
        self._on_log = on_log or (lambda m, l: None)
        self._on_job = on_job_update or (lambda j: None)
        self._on_overall = on_overall or (lambda d, t: None)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._completed = 0
        self._total = 0

    # -- public API ---------------------------------------------------------
    def cancel(self) -> None:
        """Signal all in-flight and pending jobs to stop as soon as possible."""
        self._cancel.set()
        self._log("Cancellation requested — finishing current tracks...", "warn")

    def build_jobs(
        self,
        artist: Artist,
        albums: list[Album],
        cover_lookup: Callable[[Album], bytes | None] | None = None,
    ) -> list[TrackJob]:
        """Flatten selected albums into a list of TrackJobs with dest paths."""
        jobs: list[TrackJob] = []
        artist_name = artist.name
        for album in albums:
            if not album.tracks:
                self._log(f"'{album.title}' has no tracklist — skipping.", "warn")
                continue
            cover = cover_lookup(album) if cover_lookup else None
            album_dir = (
                self.cfg.download_root
                / _safe(artist_name)
                / album_dir_name(album.year, album.title)
            )
            total = len(album.tracks)
            for track in album.tracks:
                fname = track_file_name(track.number, track.title)
                jobs.append(
                    TrackJob(
                        artist=artist_name,
                        album=album,
                        track=track,
                        dest_path=album_dir / fname,
                        cover_art=cover,
                        _id=f"{album.title}::{track.number:02d}",
                    )
                )
            del total
        return jobs

    def run(self, jobs: list[TrackJob]) -> dict[str, int]:
        """Process *jobs* concurrently. Blocks until all finish; returns stats.

        Intended to be called from a background thread by the UI layer.
        """
        self._cancel.clear()
        self._completed = 0
        self._total = len(jobs)
        self._on_overall(0, self._total)
        stats = {"done": 0, "failed": 0, "skipped": 0}

        if not jobs:
            return stats

        workers = max(1, min(self.cfg.max_workers, len(jobs)))
        self._log(f"Starting {len(jobs)} track(s) with {workers} worker(s).", "info")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._process_one, job): job for job in jobs}
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    fut.result()
                except Exception as exc:  # defensive: worker should catch its own
                    job.state = JobState.FAILED
                    job.error = str(exc)
                    self._emit(job)
                # Tally.
                if job.state == JobState.DONE:
                    stats["done"] += 1
                elif job.state == JobState.SKIPPED:
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                with self._lock:
                    self._completed += 1
                    self._on_overall(self._completed, self._total)

        self._log(
            f"Batch finished — {stats['done']} done, "
            f"{stats['skipped']} skipped, {stats['failed']} failed.",
            "info",
        )
        return stats

    # -- worker -------------------------------------------------------------
    def _process_one(self, job: TrackJob) -> None:
        """Full pipeline for a single track. Never raises (logs instead)."""
        if self._cancel.is_set():
            job.state = JobState.SKIPPED
            job.error = "cancelled"
            self._emit(job)
            return

        # Skip if a completed file already exists (idempotent re-runs).
        if job.dest_path.exists() and job.dest_path.stat().st_size > 0:
            job.state = JobState.SKIPPED
            job.progress = 1.0
            self._log(f"Already have: {job.dest_path.name}", "info")
            self._emit(job)
            return

        job.dest_path.parent.mkdir(parents=True, exist_ok=True)
        query = f"{job.artist} {job.track.title} audio"

        try:
            job.state = JobState.DOWNLOADING
            self._emit(job)
            self._download_and_convert(job, query)
        except yt_dlp.utils.DownloadError as exc:
            job.state = JobState.FAILED
            job.error = f"download error: {_short(exc)}"
            self._log(f"[{job.track.title}] {job.error}", "error")
            self._emit(job)
            return
        except Exception as exc:  # network timeout, ffmpeg, disk, etc.
            job.state = JobState.FAILED
            job.error = _short(exc)
            self._log(f"[{job.track.title}] unexpected: {job.error}", "error")
            self._emit(job)
            return

        # Tag (best-effort; never fails the job).
        try:
            job.state = JobState.TAGGING
            self._emit(job)
            tagging.embed_metadata(
                job.dest_path,
                title=job.track.title,
                artist=job.artist,
                album=job.album.title,
                year=job.album.year,
                track_number=job.track.number,
                total_tracks=len(job.album.tracks),
                cover_art=job.cover_art,
            )
        except Exception as exc:
            self._log(f"[{job.track.title}] tagging failed: {_short(exc)}", "warn")

        job.state = JobState.DONE
        job.progress = 1.0
        self._log(f"Saved: {job.dest_path.name}", "success")
        self._emit(job)

    def _download_and_convert(self, job: TrackJob, query: str) -> None:
        """Invoke yt-dlp to fetch best audio and transcode to MP3 320k."""
        # yt-dlp writes to <dest without ext>.mp3 after post-processing.
        outtmpl = str(job.dest_path.with_suffix("")) + ".%(ext)s"

        def hook(d: dict) -> None:
            if self._cancel.is_set():
                # Raising inside the hook aborts the current download cleanly.
                raise yt_dlp.utils.DownloadCancelled("cancelled by user")
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                got = d.get("downloaded_bytes", 0)
                if total:
                    job.progress = min(0.95, got / total)
                    job.state = JobState.DOWNLOADING
                    self._emit(job)
            elif status == "finished":
                job.progress = 0.97
                job.state = JobState.CONVERTING
                self._emit(job)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "default_search": "ytsearch1",  # take top search hit
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": config.NETWORK_TIMEOUT,
            "retries": config.DOWNLOAD_RETRIES,
            "fragment_retries": config.DOWNLOAD_RETRIES,
            "progress_hooks": [hook],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": config.AUDIO_FORMAT,
                    "preferredquality": self.cfg.bitrate,
                }
            ],
        }
        if self.cfg.ffmpeg_location:
            ydl_opts["ffmpeg_location"] = self.cfg.ffmpeg_location

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Passing the search query lets default_search resolve it to a video.
            ydl.download([query])

        if not job.dest_path.exists():
            raise FileNotFoundError(
                "conversion produced no output (track may be unavailable)"
            )

    # -- callback plumbing --------------------------------------------------
    def _emit(self, job: TrackJob) -> None:
        self._on_job(job)

    def _log(self, message: str, level: str = "info") -> None:
        self._on_log(message, level)


# -- module-local helpers ----------------------------------------------------
def _safe(name: str) -> str:
    from .utils import sanitize_filename
    return sanitize_filename(name, "Unknown Artist")


def _short(exc: Exception, limit: int = 160) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")
