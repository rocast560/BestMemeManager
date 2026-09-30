# reelgrab archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A native Windows PySide6 app that stores reels in folders and sends any clip into Discord desktop via Ctrl+C, drag, or a global-hotkey picker, always as a phone-playable H.264 file under 10 MB.

**Architecture:** New package `reelgrab/archive/`. Logic modules (`paths`, `store`, `library`, `media`, `sending`, `hotkeyspec`) have no Qt dependency except `sending`'s clipboard helpers, and are unit tested. Qt layers (`jobs`, `hotkey`, `ui/*`, `app`) sit on top. It reuses `reelgrab.downloader` / `reelgrab.extractor` for downloads and `downloader.make_compatible` for H.264 conversion.

**Tech Stack:** Python 3.10+, PySide6 ≥ 6.6 (QtWidgets, QtMultimedia), sqlite3, ffmpeg/ffprobe, Win32 via ctypes, pytest (+ pytest-qt for the clipboard test).

**Spec:** `docs/superpowers/specs/2026-09-29-meme-archive-design.md`

## Global Constraints

- Windows is the only supported OS. Win32 calls must be guarded so importing a module on another OS doesn't crash.
- PySide6 must only be imported inside `reelgrab/archive/` (the Docker image must not need it). The dependency goes under the `archive` extra.
- Archive root: `REELGRAB_ARCHIVE_DIR` env var, then settings, then `<repo>/downloads`. Data lives in `<root>/.reelgrab/` (`archive.db`, `thumbs/`, `send/`, `trash/`).
- Send limit defaults to 10 MB (10 × 1024 × 1024 bytes). Shrink target is 95% of the limit, with at most 2 retries at 85% bitrate.
- Never overwrite a file. Collisions get a ` (2)`, ` (3)`… suffix.
- Default hotkey is `Ctrl+Shift+M`. In the picker, Enter copies and auto-pastes; Shift+Enter only copies.
- Drag-out offers **CopyAction only**, so dropping on Explorer can never move an archive file.
- The existing 26 tests keep passing.
- Not a git repo, so there are no commit steps. Each task ends with a test run.

## Review Focus

1. Filenames with spaces, emoji, and non-ASCII characters (Instagram usernames and captions): scan, move, ffmpeg, clipboard, and drag must all work → a test uses `"😂 cat (1).mp4"`.
2. Dropping a dragged clip on Explorer or the desktop must copy, never move or delete the archive file → mime/drag actions are CopyAction only (Task 6 check plus the manual checklist).
3. Clips with no audio track: converting and shrinking must not fail → a shrink test with a silent clip.
4. A file renamed or moved in Explorer while the app is running keeps its tags and favorite → store sync reconcile test.
5. Very short clips (<1 s): the thumbnail must still be produced (fall back to the first frame) → thumbnail test with a 0.5 s clip.

---

## File Structure

| File | Responsibility |
|---|---|
| `reelgrab/archive/__init__.py` | Package marker |
| `reelgrab/archive/paths.py` | `ArchivePaths` (root, inbox, data dirs, rel/abs helpers), `default_root()` |
| `reelgrab/archive/settings.py` | `Settings` dataclass with JSON load/save at `%APPDATA%/reelgrab/settings.json` |
| `reelgrab/archive/store.py` | `Store` (SQLite: clips, tags, favorites, send log, sync, search), `Clip` |
| `reelgrab/archive/library.py` | `Library`: filesystem tree operations, trash, import, undo |
| `reelgrab/archive/media.py` | `available()`, `probe()`, `thumbnail()`, `shrink()` |
| `reelgrab/archive/sending.py` | `Sender` (which file to send, shrink cache), `make_mime()`, `copy_to_clipboard()` |
| `reelgrab/archive/hotkeyspec.py` | `parse_hotkey("Ctrl+Shift+M") -> (mods, vk)` (pure) |
| `reelgrab/archive/win32.py` | ctypes wrappers: RegisterHotKey/UnregisterHotKey, GetForegroundWindow, SetForegroundWindow, send Ctrl+V |
| `reelgrab/archive/jobs.py` | `Jobs(QObject)`: thread pool for process/download/shrink, with signals |
| `reelgrab/archive/ui/__init__.py` | Package marker |
| `reelgrab/archive/ui/grid.py` | `ClipModel`, `ClipDelegate`, `ClipGrid` (icon-mode list, hover preview, drag-out) |
| `reelgrab/archive/ui/tree.py` | `FolderTree` (pinned entries + folders, drop target, context menu) |
| `reelgrab/archive/ui/main_window.py` | `MainWindow`: top bar, splitter, downloads strip, banner, shortcuts |
| `reelgrab/archive/ui/picker.py` | `Picker`: frameless search popup |
| `reelgrab/archive/ui/settings_dialog.py` | `SettingsDialog` |
| `reelgrab/archive/ui/icons.py` | App/tray icon drawn with QPainter (no asset files) |
| `reelgrab/archive/app.py` | `main()`: wiring, tray, hotkey window, single-instance guard, file watcher |
| `start-archive.bat` | Creates `.venv` if missing, installs `.[archive]`, runs `pythonw -m reelgrab.archive.app` |
| `tests/test_archive.py` | Logic tests plus the Windows clipboard test |
| Modify `pyproject.toml` | `archive` extra, script, packages |
| Modify `.gitignore`, `.dockerignore` | Ignore `.venv/` |
| Modify `README.md` | Archive section plus manual checklist |

Inbox view = the contents of `_inbox/` **plus** loose mp4s in the root folder (the Docker web UI saves there). Nothing is moved automatically.

---

### Task 1: paths, settings, store

**Files:**
- Create: `reelgrab/archive/__init__.py`, `paths.py`, `settings.py`, `store.py`
- Test: `tests/test_archive.py`

**Interfaces (produces):**
```python
# paths.py
def default_root(settings_root: str | None = None) -> Path
class ArchivePaths:
    root: Path; inbox: Path; data: Path; db: Path; thumbs: Path; send: Path; trash: Path
    INBOX = "_inbox"
    def __init__(self, root: Path) -> None
    def ensure(self) -> None
    def rel(self, p: Path | str) -> str      # posix relative path
    def abs(self, rel: str) -> Path
# settings.py
@dataclass class Settings: hotkey="Ctrl+Shift+M"; limit_mb=10.0; auto_paste=True; archive_dir: str | None=None
    @classmethod def load(cls, path: Path | None = None) -> "Settings"; def save(self, path: Path | None = None) -> None
    @property def limit_bytes(self) -> int
# store.py
@dataclass class Clip: rel_path, size, mtime, duration=None, width=None, height=None, vcodec=None,
                       favorite=False, send_count=0, last_sent=None, tags: list[str]
    @property def folder(self) -> str; @property def name(self) -> str
class Store:
    def __init__(self, db_path: Path)
    def sync(self, files: dict[str, tuple[int, float]]) -> tuple[list[str], list[str]]  # (added_or_changed, removed)
    def get(self, rel) -> Clip | None; def all(self) -> list[Clip]
    def rename_prefix(self, old: str, new: str) -> None   # file or folder
    def remove(self, rel) -> None
    def set_probe(self, rel, duration, width, height, vcodec) -> None
    def set_tags(self, rel, tags: list[str]) -> None; def set_favorite(self, rel, fav: bool) -> None
    def log_send(self, rel, ts: float | None = None) -> None
    def search(self, query: str) -> list[Clip]
    def favorites(self) -> list[Clip]; def recent(self, limit: int = 50) -> list[Clip]
```
The store is thread-safe (`check_same_thread=False` plus an RLock). Rows under `.reelgrab/` (trash) are ignored by `sync`, `all`, `search`, `favorites`, and `recent`. A corrupt db is renamed to `archive.db.bad-<ts>` and recreated.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_archive.py
import os, shutil, subprocess, sys, time
from pathlib import Path
import pytest
from reelgrab.archive.paths import ArchivePaths
from reelgrab.archive.store import Store

HAS_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ff = pytest.mark.skipif(not HAS_FF, reason="ffmpeg not installed")


@pytest.fixture
def ap(tmp_path):
    p = ArchivePaths(tmp_path / "root"); p.ensure(); return p


def test_paths_rel_abs(ap):
    f = ap.root / "a b" / "😂 x.mp4"
    assert ap.rel(f) == "a b/😂 x.mp4" and ap.abs("a b/😂 x.mp4") == f
    assert ap.inbox.is_dir() and ap.thumbs.is_dir() and ap.send.is_dir() and ap.trash.is_dir()


def test_store_sync_reconciles_moves_and_keeps_tags(ap):
    s = Store(ap.db)
    s.sync({"_inbox/cat.mp4": (100, 1.0), "_inbox/dog.mp4": (200, 2.0)})
    s.set_tags("_inbox/cat.mp4", ["cat", "lol"]); s.set_favorite("_inbox/cat.mp4", True)
    added, removed = s.sync({"memes/cat.mp4": (100, 1.0), "_inbox/dog.mp4": (200, 2.0)})
    assert added == [] and removed == []          # treated as a move
    c = s.get("memes/cat.mp4")
    assert c.tags == ["cat", "lol"] and c.favorite and s.get("_inbox/cat.mp4") is None
    added, removed = s.sync({"memes/cat.mp4": (100, 1.0)})
    assert removed == ["_inbox/dog.mp4"]


def test_store_rename_prefix_folder(ap):
    s = Store(ap.db)
    s.sync({"a/x.mp4": (1, 1.0), "a/b/y.mp4": (2, 1.0), "ab/z.mp4": (3, 1.0)})
    s.set_tags("a/b/y.mp4", ["t"])
    s.rename_prefix("a", "c")
    assert {c.rel_path for c in s.all()} == {"c/x.mp4", "c/b/y.mp4", "ab/z.mp4"}
    assert s.get("c/b/y.mp4").tags == ["t"]


def test_store_search_and_ordering(ap):
    s = Store(ap.db)
    s.sync({"reactions/sure buddy.mp4": (1, 1.0), "cats/sure cat.mp4": (2, 1.0), "cats/other.mp4": (3, 1.0)})
    s.set_tags("cats/other.mp4", ["sure"])
    s.log_send("cats/sure cat.mp4", 10); s.log_send("cats/sure cat.mp4", 20); s.log_send("reactions/sure buddy.mp4", 30)
    assert [c.rel_path for c in s.search("sure")] == ["cats/sure cat.mp4", "reactions/sure buddy.mp4", "cats/other.mp4"]
    assert [c.rel_path for c in s.search("SURE cats")] == ["cats/sure cat.mp4", "cats/other.mp4"]
    assert [c.rel_path for c in s.recent()] == ["reactions/sure buddy.mp4", "cats/sure cat.mp4"]


def test_store_corrupt_db_is_rebuilt(ap):
    ap.db.write_bytes(b"not a database at all" * 100)
    s = Store(ap.db)
    s.sync({"x.mp4": (1, 1.0)})
    assert s.get("x.mp4") and list(ap.data.glob("archive.db.bad-*"))


def test_settings_roundtrip(tmp_path):
    from reelgrab.archive.settings import Settings
    p = tmp_path / "s.json"
    Settings(hotkey="Ctrl+Alt+K", limit_mb=25, auto_paste=False).save(p)
    s = Settings.load(p)
    assert (s.hotkey, s.limit_bytes, s.auto_paste) == ("Ctrl+Alt+K", 25 * 1024 * 1024, False)
    assert Settings.load(tmp_path / "missing.json").hotkey == "Ctrl+Shift+M"
```

- [ ] **Step 2: Run the tests and confirm they fail.** `.venv/Scripts/python -m pytest tests/test_archive.py -q`, expected: ModuleNotFoundError.
- [ ] **Step 3: Implement `paths.py`, `settings.py`, `store.py`.** Schema:

```sql
CREATE TABLE IF NOT EXISTS clips(rel_path TEXT PRIMARY KEY, size INTEGER, mtime REAL, duration REAL,
  width INTEGER, height INTEGER, vcodec TEXT, favorite INTEGER DEFAULT 0,
  send_count INTEGER DEFAULT 0, last_sent REAL);
CREATE TABLE IF NOT EXISTS tags(rel_path TEXT REFERENCES clips(rel_path) ON UPDATE CASCADE ON DELETE CASCADE,
  tag TEXT, PRIMARY KEY(rel_path, tag));
```
`sync` logic: `new = files - db`, `gone = db - files`. For each gone row, if a new rel has the same basename and size, rename the row. Otherwise delete it. Remaining new rows get inserted. Existing rows whose size or mtime changed are updated, their probe columns cleared, and they are reported as changed. The first return value = new + changed. Search: lowercase tokens; the haystack = rel_path + " " + tags. A clip matches when every token is in the haystack. Order by `send_count DESC, COALESCE(last_sent,0) DESC, rel_path`. `recent` = `last_sent IS NOT NULL ORDER BY last_sent DESC`.
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 2: library (filesystem operations and undo)

**Files:** Create `reelgrab/archive/library.py`; add tests to `tests/test_archive.py`.

**Interfaces:**
- Consumes: `ArchivePaths`, `Store`
- Produces:
```python
class Library:
    def __init__(self, paths: ArchivePaths, store: Store)
    def scan(self) -> tuple[list[str], list[str]]         # walks root (skips .reelgrab), store.sync; returns (added_or_changed, removed)
    def folders(self) -> list[str]                          # sorted rel dirs incl "_inbox", excl ".reelgrab"
    def clips_in(self, folder: str) -> list[str]            # direct mp4 children; folder "_inbox" also returns root-level mp4s
    def mkdir(self, parent: str, name: str) -> str
    def rename(self, rel: str, new_name: str) -> str
    def move(self, rels: list[str], dest_folder: str) -> list[str]   # returns new rels
    def trash(self, rels: list[str]) -> None
    def import_files(self, sources: list[Path], dest_folder: str) -> list[str]
    def undo(self) -> bool
def unique_path(dest: Path) -> Path
```
Every move, rename, and trash is recorded as one undo entry: a list of `(src_rel, dst_rel)`, applied with `os.replace` + `store.rename_prefix`. Undo applies the reversed pairs in reverse order. Trash destination: `.reelgrab/trash/<epoch_ms>/<rel>`. Import copies files (`shutil.copy2`) and doesn't add an undo entry. Names are validated: no `/ \ : * ? " < > |` and not empty (raises `ValueError`).

- [ ] **Step 1: Write failing tests**

```python
from reelgrab.archive.library import Library


def _touch(ap, rel, data=b"x"):
    p = ap.abs(rel); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p


@pytest.fixture
def lib(ap):
    return Library(ap, Store(ap.db))


def test_scan_folders_and_inbox_includes_root(ap, lib):
    _touch(ap, "_inbox/a.mp4"); _touch(ap, "loose.mp4"); _touch(ap, "memes/cats/😂 cat (1).mp4")
    _touch(ap, ".reelgrab/trash/1/x.mp4"); _touch(ap, "memes/notes.txt")
    added, _ = lib.scan()
    assert sorted(added) == ["_inbox/a.mp4", "loose.mp4", "memes/cats/😂 cat (1).mp4"]
    assert lib.folders() == ["_inbox", "memes", "memes/cats"]
    assert lib.clips_in("_inbox") == ["_inbox/a.mp4", "loose.mp4"]


def test_move_collision_rename_trash_undo(ap, lib):
    _touch(ap, "_inbox/a.mp4", b"1"); _touch(ap, "memes/a.mp4", b"2")
    lib.scan(); lib.store.set_tags("_inbox/a.mp4", ["keep"])
    [new] = lib.move(["_inbox/a.mp4"], "memes")
    assert new == "memes/a (2).mp4" and ap.abs(new).read_bytes() == b"1"
    assert lib.store.get(new).tags == ["keep"]
    lib.trash([new])
    assert not ap.abs(new).exists() and lib.store.get(new) is None
    assert lib.undo() and ap.abs(new).exists() and lib.store.get(new).tags == ["keep"]
    assert lib.undo() and ap.abs("_inbox/a.mp4").read_bytes() == b"1"
    assert lib.undo() is False


def test_rename_folder_and_bad_names(ap, lib):
    _touch(ap, "memes/x.mp4"); lib.scan()
    assert lib.rename("memes", "reactions") == "reactions"
    assert lib.store.get("reactions/x.mp4") is not None
    with pytest.raises(ValueError):
        lib.rename("reactions", "bad/name")
    assert lib.mkdir("reactions", "new") == "reactions/new" and ap.abs("reactions/new").is_dir()


def test_import_copies(ap, lib, tmp_path):
    src = tmp_path / "ext.mp4"; src.write_bytes(b"v")
    assert lib.import_files([src], "_inbox") == ["_inbox/ext.mp4"] and src.exists()
```
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement `library.py`.**
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 3: media (probe, thumbnail, shrink)

**Files:** Create `reelgrab/archive/media.py`; tests.

**Interfaces:**
```python
def available() -> bool
@dataclass class ProbeInfo: duration: float; width: int; height: int; vcodec: str | None; acodec: str | None
def probe(path: Path) -> ProbeInfo
def thumbnail(src: Path, dest: Path, width: int = 320) -> None     # frame at 1s, falls back to first frame
@dataclass class ShrinkResult: path: Path; size: int; low_quality: bool
def shrink(src: Path, dest: Path, limit_bytes: int) -> ShrinkResult
```
Uses `downloader.ffmpeg_path()` and `downloader.ffprobe_path()`. Shrink algorithm:
- `total_kbps = limit*0.95*8/duration/1000`.
- Audio: `128 if total_kbps >= 640 else max(24, total_kbps*0.2)` when the clip has audio, otherwise 0.
- `video_kbps = total - audio`. `low_quality = video_kbps < 300`.
- Scale so the short side is at most 720 when `video_kbps < 1500`, keeping dimensions even.
- ffmpeg args: `-c:v libx264 -preset medium -b:v {v}k -maxrate {v}k -bufsize {2v}k -pix_fmt yuv420p -c:a aac -b:a {a}k -movflags +faststart`. Use `-an` when there's no audio.
- Write to `dest.with_suffix(".part.mp4")`, then `os.replace`. If the output is over the limit, retry with `video_kbps *= 0.85` (at most 2 retries). The last attempt is kept even if it's still over.

- [ ] **Step 1: Write failing tests**

```python
def _make_clip(path, seconds=2.0, vcodec="libx264", audio=True, size="160x284"):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440"]
    cmd += ["-t", str(seconds), "-c:v", vcodec, "-b:v", "2M"]
    cmd += ["-c:a", "aac"] if audio else ["-an"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


@needs_ff
def test_probe_and_thumbnail_short_clip(tmp_path):
    from reelgrab.archive import media
    clip = _make_clip(tmp_path / "😂 short.mp4", seconds=0.5)
    info = media.probe(clip)
    assert info.vcodec == "h264" and info.acodec == "aac" and (info.width, info.height) == (160, 284)
    assert 0.3 < info.duration < 0.8
    media.thumbnail(clip, tmp_path / "t.jpg")
    assert (tmp_path / "t.jpg").stat().st_size > 0


@needs_ff
@pytest.mark.parametrize("audio", [True, False])
def test_shrink_under_limit(tmp_path, audio):
    from reelgrab.archive import media
    clip = _make_clip(tmp_path / "big.mp4", seconds=8, audio=audio, size="640x1136")
    limit = 150 * 1024
    assert clip.stat().st_size > limit
    r = media.shrink(clip, tmp_path / "out.mp4", limit)
    assert r.size <= limit and r.path.exists()
    assert media.probe(r.path).vcodec == "h264"
```
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement `media.py`.**
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 4: sending (which file to send, and the real-file clipboard)

**Files:** Create `reelgrab/archive/sending.py`; tests.

**Interfaces:**
- Consumes: `ArchivePaths`, `Store`, `media.shrink`
- Produces:
```python
class Sender:
    def __init__(self, paths: ArchivePaths, store: Store, limit_bytes: int)
    limit_bytes: int
    def cache_path(self, rel: str) -> Path       # send/<size>-<mtime_ns>-<limit>.mp4 (survives moves)
    def needs_shrink(self, rel: str) -> bool
    def send_path(self, rel: str) -> tuple[Path, str | None]   # (file, warning or None)
    def prepare(self, rel: str) -> Path          # blocking: shrink into the cache if needed
    def prune(self) -> None                      # delete cache files no current clip maps to
    def log(self, rels: list[str]) -> None
def make_mime(paths: list[Path], rels: list[str] | None = None) -> "QMimeData"   # file urls + application/x-reelgrab-rels
def copy_to_clipboard(paths: list[Path]) -> None
RELS_MIME = "application/x-reelgrab-rels"
```
`send_path`:
- Under the limit → the original, no warning.
- Over the limit with a cache file → the cache file.
- Over the limit, no cache file → the original with the warning `"<name> is over <N> MB and isn't shrunk yet - Discord may reject it"`.
- If the store marked the clip low-quality: warning `"<name> is long; the shrunk copy is low quality"`. Record this with a `<cache>.lowq` marker file next to the cache file (no store column needed).

- [ ] **Step 1: Write failing tests**

```python
def test_sender_paths(ap):
    from reelgrab.archive.sending import Sender
    s = Store(ap.db); _touch(ap, "a/small.mp4", b"x" * 10); _touch(ap, "a/big.mp4", b"x" * 5000)
    Library(ap, s).scan()
    snd = Sender(ap, s, limit_bytes=1000)
    assert snd.send_path("a/small.mp4") == (ap.abs("a/small.mp4"), None)
    p, warn = snd.send_path("a/big.mp4")
    assert p == ap.abs("a/big.mp4") and "isn't shrunk" in warn
    snd.cache_path("a/big.mp4").write_bytes(b"y" * 900)
    assert snd.send_path("a/big.mp4") == (snd.cache_path("a/big.mp4"), None)
    # moving keeps the cache (key is size+mtime, not path)
    Library(ap, s).move(["a/big.mp4"], "_inbox")
    assert snd.send_path("_inbox/big.mp4")[0] == snd.cache_path("_inbox/big.mp4")
    (ap.send / "stale.mp4").write_bytes(b"z"); snd.prune()
    assert not (ap.send / "stale.mp4").exists() and snd.cache_path("_inbox/big.mp4").exists()
    snd.log(["a/small.mp4"]); assert s.get("a/small.mp4").send_count == 1


@pytest.mark.skipif(sys.platform != "win32", reason="windows clipboard")
def test_clipboard_holds_real_files(qapp, tmp_path):
    import ctypes
    from ctypes import wintypes
    from reelgrab.archive.sending import copy_to_clipboard
    f = tmp_path / "😂 clip.mp4"; f.write_bytes(b"v")
    copy_to_clipboard([f])
    qapp.processEvents()
    u32, sh = ctypes.windll.user32, ctypes.windll.shell32
    u32.GetClipboardData.restype = wintypes.HANDLE
    sh.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    assert u32.OpenClipboard(None)
    try:
        assert u32.IsClipboardFormatAvailable(15)  # CF_HDROP
        h = u32.GetClipboardData(15)
        buf = ctypes.create_unicode_buffer(1024)
        sh.DragQueryFileW(h, 0, buf, 1024)
        assert Path(buf.value) == f
    finally:
        u32.CloseClipboard()
```
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.** `copy_to_clipboard` → `QGuiApplication.clipboard().setMimeData(make_mime(paths))`. Qt converts file urls to CF_HDROP on Windows.
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 5: hotkey spec and win32 wrappers

**Files:** Create `reelgrab/archive/hotkeyspec.py`, `reelgrab/archive/win32.py`; tests.

**Interfaces:**
```python
# hotkeyspec.py
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
def parse_hotkey(s: str) -> tuple[int, int]   # ("Ctrl+Shift+M") -> (MOD_CONTROL|MOD_SHIFT|MOD_NOREPEAT, 0x4D); raises ValueError
# win32.py  (every function is a no-op / returns False|0 off Windows)
def register_hotkey(hwnd: int, hk_id: int, mods: int, vk: int) -> bool
def unregister_hotkey(hwnd: int, hk_id: int) -> None
def foreground_window() -> int
def focus_window(hwnd: int) -> bool
def send_ctrl_v() -> None       # SendInput: releases Shift/Alt, then Ctrl down, V down, V up, Ctrl up
WM_HOTKEY = 0x0312
```
Keys supported: A–Z, 0–9, F1–F24, Space, Tab. Modifiers: Ctrl/Control, Shift, Alt, Win (case-insensitive). At least one modifier is required.

- [ ] **Step 1: Write failing tests**

```python
def test_parse_hotkey():
    from reelgrab.archive.hotkeyspec import parse_hotkey, MOD_CONTROL, MOD_SHIFT, MOD_ALT, MOD_NOREPEAT
    assert parse_hotkey("Ctrl+Shift+M") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x4D)
    assert parse_hotkey("alt + f9") == (MOD_ALT | MOD_NOREPEAT, 0x78)
    for bad in ("M", "Ctrl+", "Ctrl+Shift+Banana", ""):
        with pytest.raises(ValueError):
            parse_hotkey(bad)
```
- [ ] **Step 2: Run the tests and confirm they fail.** **Step 3: Implement both modules.** **Step 4: Run the tests and confirm they pass.**

### Task 6: jobs plus main window (tree, grid, copy, drag in and out, folder operations, downloads)

**Files:** Create `jobs.py`, `ui/__init__.py`, `ui/icons.py`, `ui/grid.py`, `ui/tree.py`, `ui/main_window.py`, a minimal `app.py` (window only).

**Interfaces:**
```python
class Jobs(QObject):
    clip_processed = Signal(str)            # rel: probe/thumb/shrink updated
    downloaded = Signal(list)               # new rels
    failed = Signal(str, str, object)       # title, message, retry callable or None
    busy = Signal(int)                      # queued job count
    def __init__(self, paths, store, sender, library, client_factory)
    def process(self, rels: list[str]) -> None     # compat → probe → thumb → pre-shrink (skips work already done)
    def download(self, url: str, dest_folder: str = "_inbox") -> None
    def wait_shrunk(self, rel: str, timeout: float = 3.0) -> bool
    def thumb_path(self, rel: str) -> Path         # thumbs/<size>-<mtime_ns>.jpg
```
Thread pool max 2 threads. The downloader shares one `IGClient` behind a lock (created lazily, because curl_cffi import is slow). If `make_compatible` rewrote the file, the job calls `store.sync` for that rel before probing.

Main window behavior (spec "Main window"):
- `FolderTree`:
  - Pinned entries: "★ Favorites", "🕘 Recent", "📥 Inbox". Then the folder hierarchy, from `library.folders()` excluding `_inbox`.
  - Accepts the `RELS_MIME` drop → `library.move`. External `file://` mp4 drops → `library.import_files`; for pinned targets, into `_inbox`.
  - Context menu: New folder, Rename, Delete, Open in Explorer (`os.startfile`).
- `ClipGrid` (QListView IconMode, ExtendedSelection, `setDragEnabled(True)`):
  - `ClipModel.mimeData` → `make_mime(send paths, rels)`. `supportedDragActions` = CopyAction only. A drag with an unready shrink copy shows the warning in the status bar.
  - Delegate: 180×320 tile with the thumbnail scaled to fit, the name elided, a duration/size badge (orange when it needs a shrink), ★ when favorite, and up to 3 tag chips.
  - Hover preview: a 350 ms QTimer, then a muted QMediaPlayer + QVideoWidget over the tile rect, looping. It stops on leave or scroll.
  - Keys: Ctrl+C = copy (waits up to 3 s for shrinks via `jobs.wait_shrunk`, then `sender.send_path`, `copy_to_clipboard`, `sender.log`). Enter/double-click = `os.startfile`. F2 rename, Del trash, F favorite, T tags (a QInputDialog with comma-separated values), Ctrl+Z undo.
  - Context menu mirrors the keys, plus "Show in Explorer" (`explorer /select,`).
- Top bar: link box + Download button, search box, settings button.
- Downloads strip: a QListWidget above the grid, hidden when empty. Each row is "⏳ url", "✅ n clip(s)" (fades after 5 s), or "❌ message [Retry]".
- A red ffmpeg banner when `media.available()` is False.
- The window closes to the tray. That part is wired in Task 8; in this task closing quits.

- [ ] **Step 1: Implement jobs and the ui modules.**
- [ ] **Step 2: Offscreen smoke check** (`QT_QPA_PLATFORM=offscreen`): build `MainWindow` on a temp archive with 2 generated clips, check both appear in the Inbox model after processing, that `model.mimeData` holds file urls with CopyAction-only drag actions, and that the tree lists the folders. Add it as `test_main_window_smoke` in `tests/test_archive.py`:

```python
@needs_ff
def test_main_window_smoke(qtbot, ap, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from reelgrab.archive.app import build
    _make_clip(ap.abs("_inbox/one.mp4")); _make_clip(ap.abs("memes/two.mp4"))
    ctx = build(ap, limit_bytes=10 * 1024 * 1024)
    qtbot.addWidget(ctx.window)
    qtbot.waitUntil(lambda: all(ctx.jobs.thumb_path(r).exists() for r in ("_inbox/one.mp4", "memes/two.mp4")), timeout=20000)
    ctx.window.show_folder("_inbox")
    m = ctx.window.grid.model()
    assert m.rowCount() == 1
    mime = m.mimeData([m.index(0, 0)])
    assert mime.urls()[0].toLocalFile().endswith("one.mp4")
    assert m.supportedDragActions() == Qt.CopyAction
    assert "memes" in ctx.window.tree.folder_items
```
- [ ] **Step 3: Run the full test suite and confirm it passes.**

### Task 7: search, tags, favorites, recent

Folded into Task 6's modules. This is the wiring on top of them:
- The search box (150 ms debounce) switches the grid to `store.search(text)`. Clearing it goes back to the selected folder.
- The Favorites and Recent pinned entries use `store.favorites()` / `store.recent()`.
- The F / T keys and menu items call `store.set_favorite` / `store.set_tags` and refresh the tile.

- [ ] **Step 1: Implement.** **Step 2: Extend the smoke test:**
```python
    ctx.store.set_tags("memes/two.mp4", ["sure"]); ctx.window.search.setText("sure")
    qtbot.waitUntil(lambda: m.rowCount() == 1 and m.rel_at(0) == "memes/two.mp4", timeout=2000)
```
- [ ] **Step 3: Run the tests.**

### Task 8: tray, global hotkey, quick-picker with auto-paste, settings, single instance

**Files:** `ui/picker.py`, `ui/settings_dialog.py`, a complete `app.py`.

**Behavior:**
- `app.main()`:
  - Single-instance guard via `QLocalServer("reelgrab-archive-<user>")`. A second launch sends "show" and exits.
  - `setQuitOnLastWindowClosed(False)`.
  - A tray icon with Open / Quit, where double-click opens. Closing the main window hides it; the first time, a tray message says "still running – Ctrl+Shift+M".
- `HotkeyWindow(QWidget)`: hidden. Its `winId()` is used for `register_hotkey`. `nativeEvent` watches for `WM_HOTKEY`; when it fires, it saves `foreground_window()` and calls `picker.popup()`. If registration fails, show a tray message and open `SettingsDialog`.
- `Picker(QWidget)` (frameless, `Qt.Tool | Qt.WindowStaysOnTopHint`, 520 px wide):
  - Search box plus a QListWidget of up to 8 rows (48 px thumbnail + name + folder). With nothing typed it shows favorites, then recent (deduplicated).
  - Up/Down move the selection. Enter → `_send(paste=settings.auto_paste)`; Shift+Enter → `_send(paste=False)`. Esc or losing focus hides it.
  - `_send`: waits up to 3 s for the shrink, copies, logs, hides, then if pasting: `focus_window(prev)` and `QTimer.singleShot(120, send_ctrl_v)`.
- `SettingsDialog`: hotkey (QKeySequenceEdit, converted to text "Ctrl+Shift+M"), limit MB (QDoubleSpinBox 1–500), auto-paste checkbox, archive folder (QLineEdit + Browse; "restart to apply"). Saving re-registers the hotkey and updates `sender.limit_bytes`, then re-queues pre-shrinking for all clips.
- The file watcher: a `QFileSystemWatcher` on root + all folders, debounced 500 ms → `library.scan()` → `jobs.process(added)` → refresh tree/grid.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Picker unit check** (offscreen) in `tests/test_archive.py`:
```python
def test_picker_lists_and_sends(qtbot, ap, monkeypatch):
    from reelgrab.archive.app import build
    _touch(ap, "memes/sure buddy.mp4", b"x" * 10)
    ctx = build(ap, limit_bytes=10 * 1024 * 1024, process=False)
    sent = []
    monkeypatch.setattr("reelgrab.archive.ui.picker.copy_to_clipboard", lambda paths: sent.append(paths))
    ctx.store.set_favorite("memes/sure buddy.mp4", True)
    ctx.picker.popup(prev_hwnd=0)
    assert ctx.picker.list.count() == 1
    ctx.picker.search.setText("zzz"); assert ctx.picker.list.count() == 0
    ctx.picker.search.setText("sure"); ctx.picker.send_current(paste=False)
    assert sent and sent[0][0].name == "sure buddy.mp4" and ctx.store.get("memes/sure buddy.mp4").send_count == 1
```
- [ ] **Step 3: Run the full test suite.**

### Task 9: packaging, launcher, docs, manual checklist

**Files:** Modify `pyproject.toml`, `.gitignore`, `.dockerignore`, `README.md`. Create `start-archive.bat`.

- [ ] **Step 1: `pyproject.toml`**
```toml
[project.optional-dependencies]
dev = ["pytest", "httpx", "pytest-qt"]
archive = ["PySide6>=6.6"]

[project.scripts]
reelgrab = "reelgrab.cli:main"
reelgrab-archive = "reelgrab.archive.app:main"

[tool.setuptools]
packages = ["reelgrab", "reelgrab.archive", "reelgrab.archive.ui"]
```
- [ ] **Step 2: `start-archive.bat`**: `cd /d "%~dp0"`. If `.venv\Scripts\pythonw.exe` is missing, run `py -3 -m venv .venv` (falling back to `python -m venv .venv`) and `.venv\Scripts\python -m pip install -e .[archive]`, with a clear message when ffmpeg isn't on PATH. Then `start "" .venv\Scripts\pythonw.exe -m reelgrab.archive.app`.
- [ ] **Step 3:** Add `.venv/` to `.gitignore` and `.dockerignore`. In the README, add a "Meme archive (Windows)" section covering usage, keys, and the manual checklist from the spec.
- [ ] **Step 4:** Full test suite. Then launch the real app on the real `downloads/` folder and confirm: the 4 VP9 files convert to H.264, thumbnails appear, and Ctrl+C puts CF_HDROP on the clipboard.
- [ ] **Step 5:** Hand the manual Discord checklist to the user (drag, paste, picker auto-paste, mobile playback).
