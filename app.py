"""musicdl — desktop UI (CustomTkinter).

Run with:  python app.py

Architecture
------------
The UI thread never blocks. Every network / download operation is dispatched to
a worker thread; results are marshalled back onto the Tk event loop with
``self.after(0, ...)`` so widget mutation always happens on the main thread.
"""

from __future__ import annotations

import io
import threading
from datetime import datetime

import customtkinter as ctk
from PIL import Image

from musicdl import config
from musicdl.downloader import DownloadEngine, JobState, TrackJob
from musicdl.metadata import Album, Artist, MetadataClient, MetadataError
from musicdl.shuffle import shuffle_folder

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

STATE_COLORS = {
    JobState.PENDING: "#6b7280",
    JobState.DOWNLOADING: "#3b82f6",
    JobState.CONVERTING: "#8b5cf6",
    JobState.TAGGING: "#eab308",
    JobState.DONE: "#22c55e",
    JobState.SKIPPED: "#64748b",
    JobState.FAILED: "#ef4444",
}
LOG_COLORS = {
    "info": "#cbd5e1",
    "success": "#4ade80",
    "warn": "#facc15",
    "error": "#f87171",
}


class MusicDownloaderApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("musicdl — Music Downloader")
        self.geometry("1100x720")
        self.minsize(940, 620)

        # Backend state.
        self.cfg = config.AppConfig()
        self.cfg.ensure_dirs()
        self.meta = MetadataClient()
        self.engine: DownloadEngine | None = None

        self.current_artist: Artist | None = None
        self.albums: list[Album] = []
        self.album_checks: dict[str, ctk.CTkCheckBox] = {}
        self.selected_album: Album | None = None
        self._download_thread: threading.Thread | None = None

        self._build_layout()
        self._log("Ready. Search for an artist to begin.", "info")
        self._log(
            "Reminder: only download content you own or are licensed to use.",
            "warn",
        )

    # ==================================================================== #
    #  Layout                                                              #
    # ==================================================================== #
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()
        self._build_left_panel()
        self._build_right_panel()
        self._build_bottom_panel()

    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=0, pady=(0, 8))
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            bar, text="🎵 musicdl", font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, padx=(16, 12), pady=12)

        self.search_entry = ctk.CTkEntry(
            bar, placeholder_text="Enter an artist name…",
            font=ctk.CTkFont(size=14), height=38,
        )
        self.search_entry.grid(row=0, column=1, sticky="ew", pady=12)
        self.search_entry.bind("<Return>", lambda _e: self._on_search())

        self.search_btn = ctk.CTkButton(
            bar, text="Search", width=110, height=38, command=self._on_search
        )
        self.search_btn.grid(row=0, column=2, padx=12, pady=12)

        ctk.CTkButton(
            bar, text="📁 Folder", width=90, height=38,
            fg_color="#374151", hover_color="#4b5563",
            command=self._choose_folder,
        ).grid(row=0, column=3, padx=(0, 8), pady=12)

        self.shuffle_btn = ctk.CTkButton(
            bar, text="🔀 Shuffle", width=100, height=38,
            fg_color="#7c3aed", hover_color="#6d28d9",
            command=self._on_shuffle,
        )
        self.shuffle_btn.grid(row=0, column=4, padx=(0, 16), pady=12)

    def _build_left_panel(self) -> None:
        left = ctk.CTkFrame(self)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 6))
        left.grid_rowconfigure(3, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self.artist_label = ctk.CTkLabel(
            left, text="Artist results", font=ctk.CTkFont(size=15, weight="bold")
        )
        self.artist_label.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))

        self.artist_menu = ctk.CTkOptionMenu(
            left, values=["—"], command=self._on_artist_selected, state="disabled"
        )
        self.artist_menu.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        # Filter / bulk-select controls.
        controls = ctk.CTkFrame(left, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        controls.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(controls, text="Year:").grid(row=0, column=0, padx=(0, 4))
        self.year_filter = ctk.CTkEntry(controls, width=70, placeholder_text="all")
        self.year_filter.grid(row=0, column=1, padx=(0, 8))
        self.year_filter.bind("<KeyRelease>", lambda _e: self._apply_filter())

        ctk.CTkButton(controls, text="All", width=48, command=lambda: self._bulk(True)
                      ).grid(row=0, column=4, padx=2)
        ctk.CTkButton(controls, text="None", width=48, fg_color="#374151",
                      hover_color="#4b5563", command=lambda: self._bulk(False)
                      ).grid(row=0, column=5, padx=2)

        self.include_singles = ctk.CTkCheckBox(
            controls, text="incl. singles", command=self._reload_discography
        )
        self.include_singles.grid(row=0, column=2, padx=(0, 8))

        self.disco_frame = ctk.CTkScrollableFrame(left, label_text="Discography")
        self.disco_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.disco_frame.grid_columnconfigure(0, weight=1)

    def _build_right_panel(self) -> None:
        right = ctk.CTkFrame(self)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 6))
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.art_label = ctk.CTkLabel(right, text="", width=250, height=250)
        self.art_label.grid(row=0, column=0, pady=(16, 8))
        self._set_placeholder_art()

        self.album_title_label = ctk.CTkLabel(
            right, text="Select an album to preview",
            font=ctk.CTkFont(size=15, weight="bold"), wraplength=380,
        )
        self.album_title_label.grid(row=1, column=0, padx=12, pady=(0, 8))

        self.track_frame = ctk.CTkScrollableFrame(right, label_text="Tracklist")
        self.track_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.track_frame.grid_columnconfigure(0, weight=1)

    def _build_bottom_panel(self) -> None:
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        bottom.grid_columnconfigure(0, weight=1)

        row = ctk.CTkFrame(bottom, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        row.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(row, height=16)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(row, text="Idle", width=140)
        self.status_label.grid(row=0, column=1)

        self.download_btn = ctk.CTkButton(
            row, text="⬇ Download selected", width=170,
            fg_color="#16a34a", hover_color="#15803d",
            command=self._on_download, state="disabled",
        )
        self.download_btn.grid(row=0, column=2, padx=(10, 4))

        self.cancel_btn = ctk.CTkButton(
            row, text="Cancel", width=90, fg_color="#b91c1c",
            hover_color="#991b1b", command=self._on_cancel, state="disabled",
        )
        self.cancel_btn.grid(row=0, column=3)

        self.log_box = ctk.CTkTextbox(bottom, height=140, font=ctk.CTkFont(size=12))
        self.log_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 12))
        self.log_box.configure(state="disabled")

    # ==================================================================== #
    #  Search + discography                                                #
    # ==================================================================== #
    def _on_search(self) -> None:
        query = self.search_entry.get().strip()
        if not query:
            return
        self._set_busy(True, f"Searching “{query}”…")
        threading.Thread(
            target=self._search_worker, args=(query,), daemon=True
        ).start()

    def _search_worker(self, query: str) -> None:
        try:
            artists = self.meta.search_artists(query)
        except MetadataError as exc:
            self.after(0, self._search_failed, str(exc))
            return
        self.after(0, self._search_done, artists)

    def _search_done(self, artists: list[Artist]) -> None:
        self._set_busy(False, "Idle")
        if not artists:
            self._log("No artists found. Try a different spelling.", "warn")
            return
        # Build rich labels (name + type/country/description) so a band is easy
        # to tell apart from a same-named person or tribute act.
        self._artist_lookup = {}
        labels, seen = [], {}
        for a in artists:
            label = a.display_label
            if label in seen:                 # still-identical labels -> add id
                label = f"{label} [{a.id[:6]}]"
            seen[label] = True
            labels.append(label)
            self._artist_lookup[label] = a
        self.artist_menu.configure(values=labels, state="normal")
        self.artist_menu.set(labels[0])
        self._log(
            f"Found {len(artists)} result(s). If the top match is wrong, "
            "pick the right one (Group = band) from the dropdown.",
            "info",
        )
        self._on_artist_selected(labels[0])

    def _search_failed(self, message: str) -> None:
        self._set_busy(False, "Idle")
        self._log(message, "error")

    def _on_artist_selected(self, label: str) -> None:
        artist = getattr(self, "_artist_lookup", {}).get(label)
        if not artist:
            return
        self.current_artist = artist
        self._reload_discography()

    def _reload_discography(self) -> None:
        if not self.current_artist:
            return
        self._set_busy(True, "Loading discography…")
        include = bool(self.include_singles.get())
        threading.Thread(
            target=self._disco_worker,
            args=(self.current_artist, include),
            daemon=True,
        ).start()

    def _disco_worker(self, artist: Artist, include_singles: bool) -> None:
        try:
            albums = self.meta.get_discography(artist.id, include_singles)
        except MetadataError as exc:
            self.after(0, self._search_failed, str(exc))
            return
        self.after(0, self._disco_done, albums)

    def _disco_done(self, albums: list[Album]) -> None:
        self._set_busy(False, "Idle")
        self.albums = albums
        self._render_discography()
        self.download_btn.configure(state="normal" if albums else "disabled")
        self._log(
            f"{self.current_artist.name}: {len(albums)} release(s) found.", "success"
        )

    def _render_discography(self) -> None:
        for child in self.disco_frame.winfo_children():
            child.destroy()
        self.album_checks.clear()

        year_filter = self.year_filter.get().strip()
        shown = 0
        for album in self.albums:
            if year_filter and (album.year or "") != year_filter:
                continue
            shown += 1
            rowf = ctk.CTkFrame(self.disco_frame, fg_color="transparent")
            rowf.grid(sticky="ew", pady=2)
            rowf.grid_columnconfigure(1, weight=1)

            chk = ctk.CTkCheckBox(rowf, text="", width=24)
            chk.grid(row=0, column=0, padx=(2, 6))
            self.album_checks[album.id] = chk

            year = album.year or "—"
            btn = ctk.CTkButton(
                rowf,
                text=f"[{year}] {album.title}  ·  {album.primary_type}",
                anchor="w", fg_color="transparent", hover_color="#1f2937",
                command=lambda a=album: self._preview_album(a),
            )
            btn.grid(row=0, column=1, sticky="ew")

        if shown == 0:
            ctk.CTkLabel(
                self.disco_frame, text="No releases match the current filter."
            ).grid(pady=12)

    def _apply_filter(self) -> None:
        self._render_discography()

    def _bulk(self, select: bool) -> None:
        for chk in self.album_checks.values():
            chk.select() if select else chk.deselect()

    # ==================================================================== #
    #  Album preview (tracklist + artwork)                                 #
    # ==================================================================== #
    def _preview_album(self, album: Album) -> None:
        self.selected_album = album
        self.album_title_label.configure(
            text=f"{album.title}  ({album.year or '—'})"
        )
        for child in self.track_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.track_frame, text="Loading…").grid(pady=8)
        self._set_placeholder_art()
        threading.Thread(
            target=self._preview_worker, args=(album,), daemon=True
        ).start()

    def _preview_worker(self, album: Album) -> None:
        try:
            self.meta.load_tracklist(album)
        except MetadataError as exc:
            self.after(0, self._log, str(exc), "warn")
        art = self.meta.get_cover_art(album)
        self.after(0, self._preview_done, album, art)

    def _preview_done(self, album: Album, art: bytes | None) -> None:
        if self.selected_album is not album:
            return  # user moved on
        for child in self.track_frame.winfo_children():
            child.destroy()
        if not album.tracks:
            ctk.CTkLabel(
                self.track_frame, text="No tracklist available for this release."
            ).grid(pady=8)
        for tr in album.tracks:
            mins = f"{tr.length_ms // 60000}:{(tr.length_ms // 1000) % 60:02d}" \
                if tr.length_ms else ""
            ctk.CTkLabel(
                self.track_frame,
                text=f"{tr.number:>2}.  {tr.title}    {mins}",
                anchor="w", justify="left",
            ).grid(sticky="ew", padx=6, pady=1)
        if art:
            self._show_art(art)

    # ==================================================================== #
    #  Download orchestration                                              #
    # ==================================================================== #
    def _on_download(self) -> None:
        if not self.current_artist:
            return
        selected = [a for a in self.albums if self._is_checked(a)]
        if not selected:
            # Fall back to the previewed album if nothing is ticked.
            if self.selected_album:
                selected = [self.selected_album]
            else:
                self._log("Select at least one album (or tick 'All').", "warn")
                return

        self._log(f"Preparing {len(selected)} album(s) for download…", "info")
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.search_btn.configure(state="disabled")
        self._download_thread = threading.Thread(
            target=self._download_worker, args=(self.current_artist, selected),
            daemon=True,
        )
        self._download_thread.start()

    def _download_worker(self, artist: Artist, albums: list[Album]) -> None:
        # Ensure tracklists + cover art are loaded for every selected album.
        for album in albums:
            try:
                self.meta.load_tracklist(album)
            except MetadataError as exc:
                self.after(0, self._log, f"{album.title}: {exc}", "warn")

        self.engine = DownloadEngine(
            self.cfg,
            on_log=lambda m, l: self.after(0, self._log, m, l),
            on_job_update=lambda j: self.after(0, self._job_update, j),
            on_overall=lambda d, t: self.after(0, self._overall_update, d, t),
        )
        jobs = self.engine.build_jobs(
            artist, albums, cover_lookup=self.meta.get_cover_art
        )
        if not jobs:
            self.after(0, self._log, "Nothing to download (no tracks).", "warn")
            self.after(0, self._download_finished, {"done": 0, "failed": 0, "skipped": 0})
            return
        stats = self.engine.run(jobs)
        self.after(0, self._download_finished, stats)

    def _job_update(self, job: TrackJob) -> None:
        color = STATE_COLORS.get(job.state, "#94a3b8")
        self.status_label.configure(
            text=f"{job.state.value}: {job.track.title[:22]}", text_color=color
        )

    def _overall_update(self, done: int, total: int) -> None:
        self.progress.set(done / total if total else 0)
        self.status_label.configure(text=f"{done}/{total}")

    def _download_finished(self, stats: dict) -> None:
        self.download_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.search_btn.configure(state="normal")
        self.progress.set(1.0 if stats.get("done") else 0.0)
        self.status_label.configure(text="Done", text_color="#4ade80")
        self._log(
            f"Complete — {stats['done']} downloaded, "
            f"{stats['skipped']} skipped, {stats['failed']} failed.",
            "success" if not stats["failed"] else "warn",
        )

    def _on_cancel(self) -> None:
        if self.engine:
            self.engine.cancel()
            self.cancel_btn.configure(state="disabled")

    # ==================================================================== #
    #  Small helpers                                                       #
    # ==================================================================== #
    def _is_checked(self, album: Album) -> bool:
        chk = self.album_checks.get(album.id)
        return bool(chk and chk.get())

    def _choose_folder(self) -> None:
        from tkinter import filedialog
        path = filedialog.askdirectory(initialdir=str(self.cfg.download_root))
        if path:
            self.cfg.download_root = config.Path(path)
            self.cfg.ensure_dirs()
            self._log(f"Download folder: {path}", "info")

    def _on_shuffle(self) -> None:
        """Flatten a chosen folder and randomly renumber its audio files."""
        from tkinter import filedialog, messagebox

        path = filedialog.askdirectory(
            initialdir=str(self.cfg.download_root),
            title="Pick a folder to shuffle (files will be moved & renamed)",
        )
        if not path:
            return
        # This rearranges files on disk, so make the user confirm first.
        ok = messagebox.askokcancel(
            "Shuffle folder?",
            "This will pull every audio file out of its sub-folders into\n"
            f"\n{path}\n\n"
            "rename them to random numbers (001, 002, …), and delete the\n"
            "emptied sub-folders. This cannot be undone.\n\nContinue?",
            icon="warning",
        )
        if not ok:
            return

        self._log(f"Shuffling folder: {path}", "info")
        self.shuffle_btn.configure(state="disabled")
        self._set_busy(True, "Shuffling…")
        threading.Thread(
            target=self._shuffle_worker, args=(path,), daemon=True
        ).start()

    def _shuffle_worker(self, path: str) -> None:
        try:
            shuffle_folder(
                path, on_log=lambda m, l: self.after(0, self._log, m, l)
            )
        except Exception as exc:  # defensive: never kill the worker silently
            self.after(0, self._log, f"Shuffle failed: {exc}", "error")
        self.after(0, self._shuffle_done)

    def _shuffle_done(self) -> None:
        self._set_busy(False, "Idle")
        self.shuffle_btn.configure(state="normal")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.search_btn.configure(state="disabled" if busy else "normal")
        self.status_label.configure(text=status)
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)

    def _set_placeholder_art(self) -> None:
        img = Image.new("RGB", (250, 250), (30, 41, 59))
        self._art_img = ctk.CTkImage(light_image=img, dark_image=img, size=(250, 250))
        self.art_label.configure(image=self._art_img, text="")

    def _show_art(self, data: bytes) -> None:
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            self._art_img = ctk.CTkImage(
                light_image=img, dark_image=img, size=(250, 250)
            )
            self.art_label.configure(image=self._art_img, text="")
        except Exception:
            self._set_placeholder_art()

    def _log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {message}\n")
        # Colour the just-inserted line.
        start = self.log_box.index("end-2l linestart")
        end = self.log_box.index("end-1l")
        tag = f"lvl{level}"
        self.log_box.tag_config(tag, foreground=LOG_COLORS.get(level, "#cbd5e1"))
        self.log_box.tag_add(tag, start, end)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


def main() -> None:
    app = MusicDownloaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
