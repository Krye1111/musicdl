# 🎵 musicdl — Desktop Music Downloader

A modern, dark-mode desktop app to search an artist, browse their discography
from **MusicBrainz**, and download tracks as **320 kbps MP3** with full ID3
metadata and embedded cover art. Built with **CustomTkinter**, **yt-dlp**,
**FFmpeg**, and **mutagen**.

> ⚖️ **Legal notice.** This tool fetches audio via yt-dlp from public streaming
> sources. Downloading copyrighted material without permission may violate that
> service's Terms of Service and applicable copyright law. Use it **only** for
> content you own, have licensed, or that is public domain / freely licensed.
> You are responsible for how you use it.

---

## Features

- 🔍 Artist search with automatic disambiguation (MusicBrainz).
- 💿 Full discography browsing — albums, EPs, optional singles.
- 🎚️ Two flows: **download the whole discography** (tick *All*) or **pick
  specific albums / filter by year**.
- 🖼️ Album artwork preview (Cover Art Archive) + tracklist preview.
- ⬇️ Concurrent, multithreaded downloads — the UI stays responsive.
- 🔁 Live per-track status, overall progress bar, and colour-coded logs.
- 🏷️ Automatic ID3 tags: title, artist, album, year, track number, cover art.
- 📁 Clean output layout:
  ```
  Downloads/[Artist]/[Year] - [Album]/[NN] - [Title].mp3
  ```
- 🛡️ Robust error handling: network timeouts, retries, missing tracks,
  idempotent re-runs (already-downloaded files are skipped).

---

## 1. Install Python dependencies

Requires **Python 3.10+**.

```bash
pip install -r requirements.txt
```

That installs: `customtkinter`, `musicbrainzngs`, `yt-dlp`, `mutagen`,
`requests`, `pillow`.

## 2. Install FFmpeg (required for MP3 conversion)

yt-dlp shells out to FFmpeg to transcode audio to MP3. Install it once:

| OS | Command |
|----|---------|
| **Windows** | `winget install Gyan.FFmpeg`  *(or)*  `choco install ffmpeg` |
| **macOS**   | `brew install ffmpeg` |
| **Debian/Ubuntu** | `sudo apt install ffmpeg` |

Verify it is on your PATH:

```bash
ffmpeg -version
```

If FFmpeg is installed somewhere not on PATH, set its folder in
`musicdl/config.py` → `AppConfig.ffmpeg_location`, or export
`MUSICDL_DOWNLOAD_DIR` to change where files are saved.

## 3. Run

```bash
python app.py
```

---

## How to use

1. Type an artist name and press **Search** (or Enter).
2. Pick the right artist from the dropdown (top match is auto-loaded).
3. The **Discography** panel fills in. Optionally:
   - Type a year in **Year** to filter.
   - Toggle **incl. singles**.
   - Click an album title to preview its **tracklist + artwork**.
4. Choose what to grab:
   - **Full discography:** click **All**, then **⬇ Download selected**.
   - **Specific albums:** tick individual checkboxes, then download.
5. Watch progress, per-track status, and the log. Use **Cancel** to stop.

Files land in `~/Music/musicdl-Downloads/` by default (change via 📁 Folder).

---

## Project structure

```
Music/
├── app.py                 # CustomTkinter UI (entry point)
├── requirements.txt
├── README.md
└── musicdl/
    ├── __init__.py
    ├── config.py          # tunables: bitrate, workers, paths, timeouts
    ├── utils.py           # filename sanitising, path builders, ffmpeg check
    ├── metadata.py        # MusicBrainz discography + Cover Art Archive
    ├── downloader.py      # yt-dlp + FFmpeg engine (threaded, callback-based)
    └── tagging.py         # mutagen ID3 embedding
```

The backend (`musicdl/`) is fully UI-agnostic — it communicates through plain
callbacks, so it can also be driven from a CLI or tests.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| *"conversion produced no output"* | FFmpeg missing / not on PATH — see step 2. |
| Downloads all fail with network errors | Check your connection; yt-dlp may need updating: `pip install -U yt-dlp`. |
| No artwork shown | Not every release has art in the Cover Art Archive — this is normal. |
| Slow discography load | MusicBrainz rate-limits to ~1 req/sec; large catalogs take a moment. |
| Wrong track downloaded | yt-dlp picks the top search hit; results depend on source availability. |
