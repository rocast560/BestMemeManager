# reelgrab archive: meme library for fast sending to Discord

Status: draft, awaiting approval
Date: 2026-09-29

## Goal

A native Windows app for storing reels/memes in folders and getting any clip into the
Discord desktop app in seconds: select + Ctrl+C then Ctrl+V, drag and drop, or a global
hotkey picker. Every clip sent must play on Discord mobile (H.264/AAC, under 10 MB).

**Success looks like:** press Ctrl+Shift+M in Discord, type "sure", press Enter, and the
clip is posted into the message box, playable on phones.

### Assumptions
- Single user, one Windows PC, Discord desktop app.
- The existing Docker web UI and CLI keep working unchanged. The archive is a separate
  entry point that runs on the host (not in Docker) because clipboard and drag-out need
  the native OS.
- Python 3.10+ and ffmpeg are installed on the host (already true on this machine).

### Out of scope (v1)
- Phone or browser access to the archive, multi-user use, cloud sync.
- Editing or trimming clips, GIF conversion, AI or auto-tagging.
- macOS and Linux (the code should not break there, but only Windows is supported).

## Research notes
- [Meme-File](https://github.com/Enver-Onur-Cogalan/meme-file) (Electron + SQLite) is the
  closest existing tool: tag chips, hover previews, drag or Ctrl+C into Discord. It
  confirms the interaction model works.
- [Meme-Search](https://github.com/neonwatty/meme-search) (Docker web app) supports
  subfolders and tags but is built for finding memes, not sending them.
- A web page cannot put a video file on the clipboard or drag a real file into Discord
  (the drop only carries a URL). This is why the archive is a native app.

## Architecture

New package `reelgrab/archive/`, installed as an optional extra and started with a new
console script.

- `pyproject.toml`
  - Add `archive = ["PySide6>=6.6"]` to the optional dependencies.
  - Add the script `reelgrab-archive = "reelgrab.archive.app:main"`.
  - Change `packages` to include `reelgrab.archive` and `reelgrab.archive.ui`.
- `start-archive.bat` launches it with `pythonw` (no console window).
- The Docker image is unaffected. PySide6 is imported only inside `reelgrab/archive/`.

### Units

| Module | Responsibility | Depends on |
|---|---|---|
| `archive/paths.py` | Archive root (`<repo>/downloads`, overridable with `REELGRAB_ARCHIVE_DIR`), `_inbox`, and the `.reelgrab/` data directory (`archive.db`, `thumbs/`, `send/`, `trash/`). | stdlib |
| `archive/library.py` | Scans the folder tree (`*.mp4`, skipping `.reelgrab/`), creates/renames/moves/deletes folders and clips. Delete moves to `trash/`. Name collisions get a `(2)` suffix. | paths, store |
| `archive/store.py` | SQLite database: clips (rel_path, size, mtime, duration, width, height, codec), tags, favorites, and the send log. Updated on every move or rename made by the app. On rescan, a file that disappeared and a new file with the same name and size are treated as a move, keeping its tags. | stdlib sqlite3 |
| `archive/media.py` | ffmpeg/ffprobe helpers: probe, thumbnail (JPEG at 1 s, or the first frame for short clips), phone-compat conversion (reuses `downloader.make_compatible`), and `shrink()` to under 10 MB. | downloader |
| `archive/sending.py` | `send_path(clip)` returns the original, or the cached shrunk copy when the original is over the limit. `copy_files(paths)` puts a real file list on the clipboard (Qt `QMimeData.setUrls`, which Windows exposes as CF_HDROP, the same format Explorer uses). `drag_mime(paths)` builds the drag payload. Also logs each send. | media, store, Qt |
| `archive/jobs.py` | Background work queue (QThreadPool): downloads, conversions, thumbnails, pre-shrinking. It reports progress and results back to the UI through signals. | media, library, downloader, extractor |
| `archive/hotkey.py` | Global hotkey using the Win32 `RegisterHotKey` API (via ctypes) and a Qt native event filter. It remembers the previously focused window so the picker can paste back into it. | ctypes |
| `archive/ui/main_window.py` | The main window (layout below). | all above |
| `archive/ui/picker.py` | The quick-picker popup. | store, sending |
| `archive/ui/settings.py` | Small dialog: hotkey, archive folder, size limit (default 10 MB), auto-paste on/off. Saved to `.reelgrab/settings.json`. | paths |

## Main window

- **Top bar:** a box to paste an Instagram link and a Download button (queued in the
  background with progress), a search field, and a settings button.
- **Left:** the folder tree with expand/collapse arrows. It has fixed entries for
  ★ Favorites, Recent, and Inbox, then the real folders. Drag clips onto a folder to move
  them. Right-click gives New folder, Rename, Delete (moves to trash), and Open in Explorer.
- **Right:** a thumbnail grid for the selected folder (Recent is sorted by last sent).
  - Hovering a tile plays the clip muted (QMediaPlayer).
  - Each tile shows tag chips, a star toggle, the duration, and a size badge. The badge
    is orange when the clip will be shrunk to send.
  - Multi-select with Ctrl or Shift.
- **Keyboard:**
  - Ctrl+C copies the selected clips for sending.
  - Enter or double-click opens the clip in the default player.
  - F2 renames. Del deletes (to trash). F toggles favorite. T edits tags.
  - Ctrl+Z undoes the last move or delete.
- **Drag out:** dragging tiles to another app gives it the send files (shrunk copies where
  needed).
- **Drag in:** dropping mp4 files from Explorer imports them. They are copied into the
  selected folder, or into Inbox when a fixed entry (Favorites, Recent) is selected.
- **Search:** filters every folder as you type. A clip matches when all typed words
  appear in its filename, tags, or folder name. Results are ordered by send count, then
  most recently sent. When search is empty, the grid shows the selected folder again.

## Quick-picker

- Ctrl+Shift+M (configurable) opens a small frameless, always-on-top window centered on
  the active monitor. It has a search field and up to 8 results with thumbnails.
- With nothing typed, it shows Favorites, then Recent.
- Arrow keys move the selection.
  - **Enter** copies the clip, hides the picker, restores focus to the previous window,
    and sends Ctrl+V (Win32 `SendInput`). The clip is then waiting in Discord's message
    box; the user presses Enter to post it.
  - **Shift+Enter** only copies.
  - **Esc** or clicking outside closes the picker.
- The app keeps running in the system tray when the main window is closed, so the hotkey
  keeps working. The tray menu has Open and Quit.

## Media pipeline

- **On add** (download, import, or found at startup), in the background:
  1. Probe the clip. If it isn't H.264/AAC, convert it in place (`make_compatible`).
  2. Make a thumbnail.
  3. If it's over the size limit, pre-shrink it.
- **Shrink:**
  - Aim for 9.5 MB.
  - Video bitrate = (9.5 MB × 8 / duration) − 128 kbps audio.
  - Settings: libx264 with preset `medium`, `maxrate` = target, `bufsize` = 2× target,
    yuv420p, `+faststart`, audio AAC-LC 128k.
  - Scale down to 720p height when the video bitrate would drop below 1.5 Mbps.
  - If the result is still over 10 MB, retry at 85% of the bitrate (at most 2 retries).
  - If the video bitrate would drop below 300 kbps, mark the clip "too long to shrink
    well". It is still shrunk, and a warning shows on send.
  - Output goes to `.reelgrab/send/<hash of rel_path + mtime>.mp4` and is deleted when
    the original changes or is deleted.
- **Startup scan:** all clips are queued for steps 1–3 when needed. This converts the 4
  existing VP9 files.

## Error handling

| Situation | Behavior |
|---|---|
| ffmpeg/ffprobe not found | Banner: "ffmpeg not found – thumbnails, shrinking and mobile conversion disabled". Browsing and sending originals still work. |
| Hotkey already registered | Tray notification. The settings dialog opens with the hotkey field focused. |
| Sending while the shrink is still running | Wait up to 3 s for it to finish, otherwise send the original and show a "may be over 10 MB" toast. |
| Download, extraction, or convert error | Toast with the reelgrab error message. The job's placeholder tile shows the error with a Retry button. |
| Files changed outside the app | QFileSystemWatcher triggers a rescan (debounced at 500 ms). |
| Database missing or corrupt | Rename it to `archive.db.bad-<timestamp>` and rebuild from the filesystem. Tags are lost; this is logged. |
| Name collision on move or import | Add a ` (2)` suffix. Never overwrite. |

## Testing

- **pytest, no UI** (in `tests/test_archive.py`):
  - library: scan, move, rename, trash, undo.
  - store: CRUD, move-reconcile keeps tags, send-log ordering.
  - search: matching and ordering.
  - media: shrink on a generated 20 s clip with a limit set low (e.g. 200 KB) lands under
    the limit; a VP9 clip gets converted; thumbnail is produced.
  - `send_path` picks the right file.
  - The ffmpeg tests are skipped when ffmpeg is missing (same as the existing test).
- **Windows-only test:** `copy_files()` then read the clipboard back (CF_HDROP) and check
  it holds the expected paths.
- **Manual checklist:** these cannot be automated reliably.
  - Ctrl+C then Ctrl+V into Discord.
  - Drag into Discord.
  - Picker Enter auto-paste.
  - Clip over 10 MB arrives shrunk and plays on the Discord mobile app.
  - Hotkey still works with the main window closed (tray).
  - Folder drag-move.
- The existing 26 tests must keep passing.

## Delivery order
1. `paths`, `store`, `library`, `media`, `sending` (logic only) plus tests.
2. Main window: tree, grid, thumbnails, Ctrl+C, drag in and out, folder operations.
3. Download box and background jobs, startup scan and conversion, pre-shrink.
4. Search, tags, favorites, recent.
5. Tray, global hotkey, quick-picker with auto-paste, settings.
6. `start-archive.bat`, README section, manual checklist pass.
