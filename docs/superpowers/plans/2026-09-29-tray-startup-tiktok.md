# Desktop install, tray window, TikTok: Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans). Steps use `- [ ]`.

**Goal:** Shortcuts + launch-at-login, a tray flyout for quick send/manage, and TikTok → mp4.
**Spec:** `docs/superpowers/specs/2026-09-29-tray-startup-tiktok-design.md`
**Branch:** `tray-tiktok`. One commit per task.

## Global Constraints
- Windows-only behavior is guarded: `winapp` functions are no-ops (or return False) off Windows.
- PySide6 is imported only under `reelgrab/archive/`. `reelgrab/tiktok.py` must not import Qt,
  because Docker uses it.
- Never use TikTok's `downloadAddr` (watermarked).
- Stream rule: highest pixels → h264 over h265 → bitrate. `bytevc2` is skipped.
- Existing 56 tests stay green.

## Review Focus
1. TikTok short links that redirect to the homepage or a non-video page → clear error, not
   a crash or a download of the wrong thing.
2. A TikTok item with no `bitrateInfo` (older videos) → falls back to `playAddr`.
3. The repo path contains spaces (`random-projects` is fine, but the user's profile could
   have spaces) → the shortcut and Run-key command must quote the paths.
4. Flyout with the tray icon in the overflow area (empty geometry) → still appears on screen.
5. A web UI request for a TikTok thumbnail → the proxy allows the TikTok CDN, but only those
   hosts (not an open proxy).

---

### Task 1: TikTok extractor + download plumbing
**Files:** create `reelgrab/tiktok.py`, `tests/test_tiktok.py`,
`tests/fixtures/tiktok_video.html`. Modify `models.py` (`VideoItem.http_headers`),
`downloader.py` (`Plan.http_headers`, `fetch(headers=)`), `extractor.py` (dispatch),
`shortcode.py` (message), `web.py` (CDN hosts + referer), `static/index.html` (copy).
**Produces:**
```python
class UnsupportedMedia(ExtractionError)
def is_tiktok_url(url: str) -> bool
def parse_tiktok_url(url: str) -> tuple[str | None, bool]
def solve_waf(html: str) -> dict[str, str]
def parse_page(html: str, video_id: str) -> Media
def pick_variant(video: dict) -> tuple[VideoVariant, str]      # (variant, codec)
def extract(url: str, client) -> Media
```
- [ ] Build the fixture from the saved live page: keep only the rehydration script, with the
  video-detail item trimmed to the fields used, and CDN URLs replaced with
  `https://v16.example.tiktokcdn.com/...`.
- [ ] Write the tests (URL forms, photo rejection, parse → Media fields + headers, stream
  rule incl. playAddr fallback, bytevc2 skip and downloadAddr never chosen, statusCode
  error, `solve_waf` on a challenge generated in-test, dispatch from `extractor.extract`,
  `fetch` uses item headers, short link to the homepage errors, web thumb proxy allows
  tiktokcdn and rejects others). Run them and see them fail.
- [ ] Implement. Run and see them pass. Run the full suite. Commit.
- [ ] Live check: download yt-dlp's test video through `reelgrab` CLI → h264/aac.

### Task 2: winapp (icon, shortcuts, startup) + `--tray` + settings + bat
**Files:** create `reelgrab/archive/winapp.py`, `reelgrab/archive/assets/reelgrab.ico`.
Modify `app.py` (`--tray`), `ui/settings_dialog.py`, `start-archive.bat`, `pyproject.toml`
(package data for assets).
**Produces:**
```python
ICON_PATH: Path
def ensure_icon() -> Path
def launch_command(tray: bool) -> tuple[str, str]
def create_shortcuts(desktop: Path | None = None, start_menu: Path | None = None) -> list[Path]
def set_startup(enabled: bool, value_name: str = "reelgrab archive") -> None
def is_startup_enabled(value_name: str = "reelgrab archive") -> bool
def main(argv=None) -> int     # python -m reelgrab.archive.winapp --shortcuts
```
- [ ] Tests: the shortcut goes to a tmp dir and is read back via PowerShell COM (target,
  args, working dir, icon); startup set/unset under the value name `reelgrab-test` and
  the stored command is quoted and contains `--tray`; `launch_command` quoting with
  spaces in the paths. Run → fail.
- [ ] Implement. Run → pass. Commit.

### Task 3: ClipActions + flyout + tray wiring
**Files:** create `reelgrab/archive/ui/actions.py` (`ClipActions`: copy, favorite, tags,
move, trash, undo — logic moved out of `MainWindow`), `reelgrab/archive/ui/flyout.py`.
Modify `ui/grid.py` (compact mode), `ui/main_window.py` (use ClipActions), `app.py` (tray
Trigger → flyout, `Ctx.flyout`).
**Produces:**
```python
class ClipActions(QObject):
    notify = Signal(str); changed = Signal()
    def copy(self, rels) -> None; def toggle_favorite(self, rels) -> None
    def edit_tags(self, rels, parent) -> None; def move(self, rels, dest) -> None
    def trash(self, rels) -> None; def undo(self) -> None
    def menu(self, rels, parent) -> QMenu
class Flyout(QWidget):
    def popup(self, anchor: QRect | None) -> None
    tabs; folder_box; search; grid; link; status
```
- [ ] Tests (offscreen): favorites/recent/folder tab contents; folder dropdown lists folders;
  search overrides the tab; click copies (monkeypatched clipboard) and logs a send;
  `ClipActions.move`/`trash` + `undo` via the flyout; popup with an empty anchor lands
  inside the available screen area; the main window smoke test still passes. Run → fail.
- [ ] Implement. Run → pass. Full suite. Commit.

### Task 4: docs, full verification, review, merge
- [ ] README: TikTok, tray window, startup/shortcuts.
- [ ] Full suite. Rebuild Docker and check a TikTok download through the web API. Relaunch
  the archive (`--tray`), create shortcuts, and enable startup for real.
- [ ] Fresh-reviewer pass. Fix Critical/Important with a failing test first.
- [ ] Fast-forward `main` to `tray-tiktok` locally.
