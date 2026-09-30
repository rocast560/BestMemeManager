# reelgrab: desktop install, tray window, TikTok support

Status: approved in chat 2026-09-29 ("yes execute")
Builds on: `2026-09-29-meme-archive-design.md`

## Goals
1. The archive behaves like an installed Windows app: Desktop and Start-menu shortcuts, and
   an option to launch at login, hidden in the tray.
2. Clicking the tray icon (next to wifi) opens a compact window to quickly find, send, and
   manage clips.
3. TikTok links download to phone-playable mp4s exactly like Instagram reels do, in the web
   UI, the CLI, and the archive.

Out of scope: a standalone .exe/installer, TikTok photo slideshows, TikTok login/private
videos, auto-paste from the tray window.

## 1. Desktop install and startup

New module `reelgrab/archive/winapp.py` (Windows only; no-ops elsewhere):
- `ICON_PATH = <repo>/reelgrab/archive/assets/reelgrab.ico`. It's generated from
  `icons.app_icon()` by `ensure_icon()` if missing, and committed.
- `launch_command(tray: bool) -> tuple[str, str]`: (target, args) =
  (`<repo>\.venv\Scripts\pythonw.exe`, `-m reelgrab.archive.app [--tray]`). Uses
  `sys.executable`'s `pythonw.exe` sibling when not running from `.venv`.
- `create_shortcuts(desktop: Path | None = None, start_menu: Path | None = None) -> list[Path]`:
  writes `reelgrab archive.lnk` (target, args, working dir = repo, icon) through the
  `WScript.Shell` COM object via PowerShell. Defaults come from the known-folder
  Desktop/Programs paths.
- `set_startup(enabled: bool, value_name: str = "reelgrab archive")` /
  `is_startup_enabled(value_name=...) -> bool`: the
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value, set to the quoted target plus
  args with `--tray`. Uses `winreg`.
- `app.main()` accepts `--tray`: the main window isn't shown on start.
- Settings dialog: a "Launch at Windows startup" checkbox (reads and writes the registry
  directly; not stored in settings.json) and a "Create Desktop & Start-menu shortcuts" button.
- `start-archive.bat`: on first run (when the venv is created), calls
  `python -m reelgrab.archive.winapp --shortcuts`.

## 2. Tray window (`reelgrab/archive/ui/flyout.py`)

- Tray left-click (`QSystemTrayIcon.Trigger`) toggles the `Flyout`. Double-click opens the
  main window. Right-click shows the existing menu.
- Frameless `Qt.Tool | WindowStaysOnTopHint` window, 380×560. It's positioned above the tray
  icon (`tray.geometry()`), clamped to the screen's available area; if the icon geometry is
  empty (overflow area), it goes to the bottom-right corner. It hides when it loses focus.
- Layout, top to bottom:
  - A link box ("Paste an Instagram or TikTok link") with a one-line download status.
  - A search box.
  - Tabs: ★ Favorites | 🕘 Recent | 📁 Folder. The Folder tab shows a folder dropdown
    (Inbox + all folders, remembering the last choice).
  - A clip grid: `ClipGrid` with a compact tile size (3 columns: 112×200 tiles, 150 px image,
    no tag chips, no hover preview).
  - A footer with an "Open full window" button.
- Search, when not empty, overrides the tab (same `store.search`).
- Clicking a clip copies it for sending (same path as Ctrl+C: wait for the shrink, copy
  the send file, log it) and shows "Copied – paste in Discord (Ctrl+V)" in the status line.
  Dragging out works as in the main grid (copy only).
- Right-click menu: Favorite, Tags…, Move to ▸ (folders), Delete (trash, undoable with
  Ctrl+Z in either window). These share code with the main window through a small
  `ClipActions` helper (moved out of `MainWindow`), so there's no duplicated logic.
- Refreshes on `jobs.clip_processed`, on download done, and when it's shown.

## 3. TikTok (`reelgrab/tiktok.py`)

- **URLs**
  - `is_tiktok_url(url)` matches host `tiktok.com` / `*.tiktok.com` (incl. `vm.`, `vt.`,
    `m.`).
  - `parse_tiktok_url(url) -> (video_id | None, needs_resolve: bool)` handles:
    - `/@user/video/<id>`
    - `/embed/v2/<id>` and `/embed/<id>`
    - `/share/video/<id>`
    - `/@user/photo/<id>` → raises `UnsupportedMedia("photo posts aren't supported")`
    - short links `vm.tiktok.com/<code>`, `vt.tiktok.com/<code>`, `tiktok.com/t/<code>` →
      `needs_resolve`
  - Resolving follows the redirect without auto-follow (up to 3 hops). If it ends somewhere
    with no video id: `ExtractionError("that TikTok link doesn't point to a video")`.
- **Fetch:** `GET https://www.tiktok.com/@_/video/<id>` (TikTok redirects to the real
  username) with the shared impersonating session. If the page has no rehydration script
  but has the WAF challenge markers (`id="cs"`, `id="wci"`, `id="rci"`, `id="rs"`), solve it
  and retry once.
- **Challenge solver:** a pure function `solve_waf(page_html) -> dict[str, str]` returning
  the cookies to set. It ports yt-dlp's algorithm (base64 JSON challenge, brute-force the
  SHA-256 input up to 1,000,001 iterations).
- **Parse:** `__UNIVERSAL_DATA_FOR_REHYDRATION__` → `__DEFAULT_SCOPE__` →
  `webapp.video-detail`.
  - `statusCode != 0` → `ExtractionError` with the status message (e.g. private/removed).
  - `itemInfo.itemStruct` gives: `id`, `author.uniqueId`, `desc`, `video.duration`,
    `video.cover`.
  - An item with `imagePost` → `UnsupportedMedia`.
- **Stream choice:** candidates are each `video.bitrateInfo[].PlayAddr` (codec from
  `CodecType`/`UrlKey`: `h264`, `h265`/`bytevc1`; skip `bytevc2`), first URL of `UrlList`,
  plus `video.playAddr` (h264, `video.width/height`) as a fallback.
  - **Rule:** highest pixel count, then h264 over h265, then bitrate.
  - `downloadAddr` (watermarked) is never used.
- **Output:** `Media(shortcode=<id>, username=<uniqueId>, caption=desc,
  videos=[VideoItem(variants=[VideoVariant(url, w, h)], duration, thumbnail=cover,
  http_headers={"Referer": "https://www.tiktok.com/"})], source="tiktok_web")`.
- **Plumbing:**
  - `VideoItem` gains `http_headers: dict | None = None`, and `Plan` carries it.
  - `downloader.fetch` uses it instead of the Instagram Referer/Origin when set.
  - `extractor.extract()` dispatches TikTok URLs to `tiktok.extract()` before Instagram
    parsing.
  - `InvalidReelUrl` messages become "not an Instagram or TikTok link".
  - Existing H.264 conversion handles h265 output unchanged.
- **Web UI:** placeholder and subtitle mention TikTok. `CDN_HOSTS` adds `.tiktokcdn.com`,
  `.tiktokcdn-us.com`, `.tiktokcdn-eu.com`, `.ibyteimg.com` for the thumbnail proxy (the
  proxy request sends the TikTok Referer for those hosts).

## Errors
| Situation | Behavior |
|---|---|
| Photo post / slideshow | "TikTok photo posts aren't supported – only videos" |
| Private / removed video | `ExtractionError("tiktok says: <statusMsg or code>")` |
| Challenge not solvable or still served after retry | `ExtractionError("tiktok blocked the request (bot check) – try again in a minute")` |
| CDN 403 | the existing "cdn returned http 403 (url may have expired, re-extract)" |
| Registry write fails | Settings shows the error; the checkbox reverts |
| Shortcut creation fails | Settings / bat shows the PowerShell error text |

## Testing
- **Offline** (`tests/test_tiktok.py`), using a trimmed fixture built from a real page
  (`tests/fixtures/tiktok_video.html`, URLs anonymized):
  - URL parsing (all forms, photo rejection, short link flag).
  - Rehydration parse → Media.
  - Stream rule (resolution first, h264 tie-break, bytevc2 skipped, downloadAddr never used).
  - `statusCode` error.
  - `solve_waf` against a generated challenge.
  - `extract()` dispatch.
  - `fetch` sends the item's headers.
- **Live, opt-in** (`REELGRAB_LIVE=1`): download yt-dlp's public test video and check
  it's h264/aac after conversion.
- **Archive:** `test_archive.py` additions for `winapp` (shortcut written to a tmp dir and
  read back through WScript.Shell; startup set/unset under a test value name), and a
  flyout offscreen test (tabs, search, folder dropdown, click copies + logs, right-click
  actions go through `ClipActions`).
- Existing 56 tests keep passing. Final whole-change review by a fresh reviewer, like last
  time.
