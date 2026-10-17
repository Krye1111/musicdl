"""Discography + artwork lookup via MusicBrainz and the Cover Art Archive.

MusicBrainz gives us a clean, free, structured view of an artist's releases
without needing to scrape. We use it purely as a *catalog* — the actual audio
is fetched separately by the downloader from a user-authorised source.

Data model (lightweight, UI-friendly dataclasses):
    Artist  -> id, name
    Album   -> id (release-group MBID), title, year, primary_type, tracks
    Track   -> number, title, length_ms
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import musicbrainzngs
import requests

from . import config


# --- Public data structures -------------------------------------------------
@dataclass
class Track:
    number: int
    title: str
    length_ms: int | None = None


@dataclass
class Album:
    id: str                       # MusicBrainz release-group MBID
    title: str
    year: str | None = None
    primary_type: str = "Album"   # Album / Single / EP / ...
    release_id: str | None = None  # a concrete release MBID (for tracklist)
    tracks: list[Track] = field(default_factory=list)

    @property
    def sort_key(self) -> tuple:
        return (self.year or "9999", self.title.lower())


@dataclass
class Artist:
    id: str
    name: str
    type: str = ""            # "Group" (band), "Person", "Orchestra", ...
    country: str = ""         # ISO code, e.g. "GB", "US"
    disambiguation: str = ""  # MusicBrainz's short descriptor to tell dupes apart

    @property
    def display_label(self) -> str:
        """A human-readable label that distinguishes same-named results."""
        bits = [self.name]
        tag = " · ".join(b for b in (self.type, self.country, self.disambiguation) if b)
        if tag:
            bits.append(f"({tag})")
        return " ".join(bits)


class MetadataError(Exception):
    """Raised for unrecoverable metadata-lookup failures."""


class MetadataClient:
    """Thin, rate-limited wrapper around musicbrainzngs + Cover Art Archive."""

    def __init__(self) -> None:
        musicbrainzngs.set_useragent(
            config.MB_APP_NAME, config.MB_APP_VERSION, config.MB_CONTACT
        )
        # musicbrainzngs has its own limiter, but we also throttle defensively.
        self._last_call = 0.0

    # -- internal helpers ---------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = config.MB_RATE_LIMIT_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    # -- artist search ------------------------------------------------------
    def search_artists(self, query: str, limit: int = 8) -> list[Artist]:
        """Return candidate artists for a free-text query, best match first."""
        query = (query or "").strip()
        if not query:
            return []
        try:
            self._throttle()
            result = musicbrainzngs.search_artists(artist=query, limit=limit)
        except musicbrainzngs.NetworkError as exc:
            raise MetadataError(f"Network error contacting MusicBrainz: {exc}") from exc
        except musicbrainzngs.MusicBrainzError as exc:
            raise MetadataError(f"MusicBrainz error: {exc}") from exc

        artists = []
        for item in result.get("artist-list", []):
            artists.append(
                Artist(
                    id=item["id"],
                    name=item.get("name", "Unknown"),
                    type=item.get("type", "") or "",
                    country=item.get("country", "") or "",
                    disambiguation=item.get("disambiguation", "") or "",
                )
            )
        return artists

    # -- discography --------------------------------------------------------
    def get_discography(
        self,
        artist_id: str,
        include_singles: bool = False,
    ) -> list[Album]:
        """Return the artist's release-groups (albums/EPs, optionally singles).

        Tracklists are *not* fetched here (that would be N extra calls); call
        :meth:`load_tracklist` lazily when the user expands an album.
        """
        types = ["album", "ep"]
        if include_singles:
            types.append("single")

        albums: list[Album] = []
        offset = 0
        page = 100
        try:
            while True:
                self._throttle()
                resp = musicbrainzngs.browse_release_groups(
                    artist=artist_id,
                    release_type=types,
                    limit=page,
                    offset=offset,
                )
                groups = resp.get("release-group-list", [])
                for rg in groups:
                    date = rg.get("first-release-date", "") or ""
                    year = date[:4] if len(date) >= 4 else None
                    albums.append(
                        Album(
                            id=rg["id"],
                            title=rg.get("title", "Unknown Album"),
                            year=year,
                            primary_type=rg.get("primary-type", "Album") or "Album",
                        )
                    )
                count = resp.get("release-group-count", len(groups))
                offset += page
                if offset >= count or not groups:
                    break
        except musicbrainzngs.NetworkError as exc:
            raise MetadataError(f"Network error loading discography: {exc}") from exc
        except musicbrainzngs.MusicBrainzError as exc:
            raise MetadataError(f"MusicBrainz error loading discography: {exc}") from exc

        # De-duplicate release-groups by title+year (MB sometimes lists variants).
        seen: dict[tuple, Album] = {}
        for alb in albums:
            key = (alb.title.lower(), alb.year)
            seen.setdefault(key, alb)
        return sorted(seen.values(), key=lambda a: a.sort_key)

    # -- tracklist ----------------------------------------------------------
    def load_tracklist(self, album: Album) -> Album:
        """Populate *album.tracks* (and release_id) in place, then return it."""
        if album.tracks:
            return album
        try:
            self._throttle()
            # Pick a representative release from the release-group.
            rg = musicbrainzngs.get_release_group_by_id(
                album.id, includes=["releases"]
            )["release-group"]
            releases = rg.get("release-list", [])
            if not releases:
                return album
            release_id = releases[0]["id"]
            album.release_id = release_id

            self._throttle()
            rel = musicbrainzngs.get_release_by_id(
                release_id, includes=["recordings"]
            )["release"]
            for medium in rel.get("medium-list", []):
                for tr in medium.get("track-list", []):
                    rec = tr.get("recording", {})
                    length = rec.get("length") or tr.get("length")
                    album.tracks.append(
                        Track(
                            number=int(tr.get("position", len(album.tracks) + 1)),
                            title=rec.get("title") or tr.get("title", "Unknown"),
                            length_ms=int(length) if length else None,
                        )
                    )
        except musicbrainzngs.NetworkError as exc:
            raise MetadataError(f"Network error loading tracklist: {exc}") from exc
        except musicbrainzngs.MusicBrainzError as exc:
            # Non-fatal: return what we have (album with no tracks).
            return album
        return album

    # -- cover art ----------------------------------------------------------
    def get_cover_art(self, album: Album, size: int = 250) -> bytes | None:
        """Fetch front cover art bytes for *album*, or None if unavailable."""
        # Cover Art Archive is keyed on release-group MBID.
        url = f"{config.COVER_ART_BASE}/release-group/{album.id}/front-{size}"
        try:
            resp = requests.get(url, timeout=config.NETWORK_TIMEOUT)
            if resp.status_code == 200 and resp.content:
                return resp.content
        except requests.RequestException:
            pass
        return None
