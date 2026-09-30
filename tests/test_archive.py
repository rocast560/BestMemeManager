import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from reelgrab.archive.paths import ArchivePaths
from reelgrab.archive.store import Store

HAS_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ff = pytest.mark.skipif(not HAS_FF, reason="ffmpeg not installed")


@pytest.fixture
def ap(tmp_path):
    p = ArchivePaths(tmp_path / "root")
    p.ensure()
    return p


# ---------- paths / store / settings ----------

def test_paths_rel_abs(ap):
    f = ap.root / "a b" / "😂 x.mp4"
    assert ap.rel(f) == "a b/😂 x.mp4" and ap.abs("a b/😂 x.mp4") == f
    assert ap.inbox.is_dir() and ap.thumbs.is_dir() and ap.send.is_dir() and ap.trash.is_dir()


def test_store_sync_reconciles_moves_and_keeps_tags(ap):
    s = Store(ap.db)
    s.sync({"_inbox/cat.mp4": (100, 1.0), "_inbox/dog.mp4": (200, 2.0)})
    s.set_tags("_inbox/cat.mp4", ["cat", "lol"])
    s.set_favorite("_inbox/cat.mp4", True)
    added, removed = s.sync({"memes/cat.mp4": (100, 1.0), "_inbox/dog.mp4": (200, 2.0)})
    assert added == [] and removed == []  # treated as a move
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
    s.log_send("cats/sure cat.mp4", 10)
    s.log_send("cats/sure cat.mp4", 20)
    s.log_send("reactions/sure buddy.mp4", 30)
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


def test_store_rename_prefix_underscore_folder(ap):
    s = Store(ap.db)
    s.sync({"_in_box/x.mp4": (1, 1.0)})
    s.rename_prefix("_in_box", "memes")
    assert s.get("memes/x.mp4") is not None


# ---------- library ----------

def _touch(ap, rel, data=b"x"):
    p = ap.abs(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def lib(ap):
    from reelgrab.archive.library import Library
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
    lib.scan()
    lib.store.set_tags("_inbox/a.mp4", ["keep"])
    [new] = lib.move(["_inbox/a.mp4"], "memes")
    assert new == "memes/a (2).mp4" and ap.abs(new).read_bytes() == b"1"
    assert lib.store.get(new).tags == ["keep"]
    lib.trash([new])
    assert not ap.abs(new).exists() and lib.store.get(new) is None
    assert lib.undo() and ap.abs(new).exists() and lib.store.get(new).tags == ["keep"]
    assert lib.undo() and ap.abs("_inbox/a.mp4").read_bytes() == b"1"
    assert lib.undo() is False


def test_rename_folder_and_bad_names(ap, lib):
    _touch(ap, "memes/x.mp4")
    lib.scan()
    assert lib.rename("memes", "reactions") == "reactions"
    assert lib.store.get("reactions/x.mp4") is not None
    with pytest.raises(ValueError):
        lib.rename("reactions", "bad/name")
    assert lib.mkdir("reactions", "new") == "reactions/new" and ap.abs("reactions/new").is_dir()


def test_import_copies(ap, lib, tmp_path):
    src = tmp_path / "ext.mp4"
    src.write_bytes(b"v")
    assert lib.import_files([src], "_inbox") == ["_inbox/ext.mp4"] and src.exists()


# ---------- media ----------

def _make_clip(path, seconds=2.0, vcodec="libx264", audio=True, size="160x284"):
    path.parent.mkdir(parents=True, exist_ok=True)
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


# ---------- sending ----------

def test_sender_paths(ap):
    from reelgrab.archive.library import Library
    from reelgrab.archive.sending import Sender
    s = Store(ap.db)
    _touch(ap, "a/small.mp4", b"x" * 10); _touch(ap, "a/big.mp4", b"x" * 5000)
    Library(ap, s).scan()
    snd = Sender(ap, s, limit_bytes=1000)
    assert snd.send_path("a/small.mp4") == (ap.abs("a/small.mp4"), None)
    p, warn = snd.send_path("a/big.mp4")
    assert p == ap.abs("a/big.mp4") and "isn't shrunk" in warn
    snd.cache_path("a/big.mp4").parent.mkdir(parents=True)
    snd.cache_path("a/big.mp4").write_bytes(b"y" * 900)
    assert snd.send_path("a/big.mp4") == (snd.cache_path("a/big.mp4"), None)
    # moving keeps the cache (key is size+mtime, not path)
    Library(ap, s).move(["a/big.mp4"], "_inbox")
    assert snd.send_path("_inbox/big.mp4")[0] == snd.cache_path("_inbox/big.mp4")
    (ap.send / "stale.mp4").write_bytes(b"z")
    snd.prune()
    assert not (ap.send / "stale.mp4").exists() and snd.cache_path("_inbox/big.mp4").exists()
    snd.log(["a/small.mp4"])
    assert s.get("a/small.mp4").send_count == 1


@pytest.mark.skipif(sys.platform != "win32", reason="windows clipboard")
def test_clipboard_holds_real_files(qapp, tmp_path):
    import ctypes
    from ctypes import wintypes
    from reelgrab.archive.sending import copy_to_clipboard
    f = tmp_path / "😂 clip.mp4"
    f.write_bytes(b"v")
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


# ---------- hotkey ----------

def test_parse_hotkey():
    from reelgrab.archive.hotkeyspec import parse_hotkey, MOD_CONTROL, MOD_SHIFT, MOD_ALT, MOD_NOREPEAT
    assert parse_hotkey("Ctrl+Shift+M") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x4D)
    assert parse_hotkey("alt + f9") == (MOD_ALT | MOD_NOREPEAT, 0x78)
    for bad in ("M", "Ctrl+", "Ctrl+Shift+Banana", ""):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


# ---------- ui (offscreen) ----------

@needs_ff
def test_main_window_smoke(qtbot, ap):
    from PySide6.QtCore import Qt
    from reelgrab.archive.app import build
    _make_clip(ap.abs("_inbox/one.mp4")); _make_clip(ap.abs("memes/two.mp4"))
    ctx = build(ap, limit_bytes=10 * 1024 * 1024)
    qtbot.addWidget(ctx.window)
    qtbot.waitUntil(lambda: all(ctx.jobs.thumb_path(r).exists() for r in ("_inbox/one.mp4", "memes/two.mp4")),
                    timeout=20000)
    ctx.window.show_folder("_inbox")
    m = ctx.window.grid.model()
    assert m.rowCount() == 1
    mime = m.mimeData([m.index(0, 0)])
    assert mime.urls()[0].toLocalFile().endswith("one.mp4")
    assert m.supportedDragActions() == Qt.CopyAction
    assert "memes" in ctx.window.tree.folder_items
    # search / tags
    ctx.store.set_tags("memes/two.mp4", ["sure"])
    ctx.window.search.setText("sure")
    qtbot.waitUntil(lambda: m.rowCount() == 1 and m.rel_at(0) == "memes/two.mp4", timeout=2000)
    ctx.shutdown()


def test_picker_lists_and_sends(qtbot, ap, monkeypatch):
    from reelgrab.archive.app import build
    _touch(ap, "memes/sure buddy.mp4", b"x" * 10)
    ctx = build(ap, limit_bytes=10 * 1024 * 1024, process=False)
    sent = []
    monkeypatch.setattr("reelgrab.archive.ui.picker.copy_to_clipboard", lambda paths: sent.append(paths))
    ctx.store.set_favorite("memes/sure buddy.mp4", True)
    ctx.picker.popup(prev_hwnd=0)
    assert ctx.picker.list.count() == 1
    ctx.picker.search.setText("zzz")
    assert ctx.picker.list.count() == 0
    ctx.picker.search.setText("sure")
    ctx.picker.send_current(paste=False)
    assert sent and sent[0][0].name == "sure buddy.mp4" and ctx.store.get("memes/sure buddy.mp4").send_count == 1
    ctx.shutdown()


# ---------- final review fixes ----------

def test_undo_never_overwrites_and_keeps_tags(ap, lib):
    _touch(ap, "_inbox/x.mp4", b"old")
    lib.scan()
    lib.store.set_tags("_inbox/x.mp4", ["keep"])
    lib.move(["_inbox/x.mp4"], "funny")
    _touch(ap, "_inbox/x.mp4", b"brand new")  # same reel downloaded again
    lib.scan()
    lib.store.set_tags("_inbox/x.mp4", ["newtag"])
    assert lib.undo()
    assert ap.abs("_inbox/x.mp4").read_bytes() == b"brand new"
    assert lib.store.get("_inbox/x.mp4").tags == ["newtag"]
    restored = [c for c in lib.store.all() if c.tags == ["keep"]]
    assert len(restored) == 1 and ap.abs(restored[0].rel_path).read_bytes() == b"old"


def test_undo_after_external_move_fails_cleanly(ap, lib):
    _touch(ap, "_inbox/x.mp4")
    lib.scan()
    lib.move(["_inbox/x.mp4"], "funny")
    ap.abs("funny/x.mp4").rename(ap.abs("funny/gone.mp4"))
    assert lib.undo() is False  # source vanished: refuse, don't crash
    assert ap.abs("funny/gone.mp4").exists()


def test_explorer_rename_keeps_tags(ap):
    s = Store(ap.db)
    s.sync({"a/cat.mp4": (100, 5.0)})
    s.set_tags("a/cat.mp4", ["cat"]); s.set_favorite("a/cat.mp4", True)
    added, removed = s.sync({"a/kitty.mp4": (100, 5.0)})
    assert (added, removed) == ([], [])
    c = s.get("a/kitty.mp4")
    assert c.tags == ["cat"] and c.favorite


def test_scan_reports_new_clips_to_callback(ap, lib):
    seen = []
    lib.on_added = seen.extend
    _touch(ap, "_inbox/new.mp4")
    lib.scan()
    assert seen == ["_inbox/new.mp4"]


def test_clips_in_hides_temp_files(ap, lib):
    _touch(ap, "_inbox/a.mp4"); _touch(ap, "_inbox/a.mp4.1f2e.tmp.mp4"); _touch(ap, "_inbox/b.part.mp4")
    assert lib.clips_in("_inbox") == ["_inbox/a.mp4"]


def test_send_copy_keeps_original_name_and_prune_is_safe(ap):
    from reelgrab.archive.library import Library
    from reelgrab.archive.sending import Sender
    s = Store(ap.db)
    _touch(ap, "a/😂 big meme.mp4", b"x" * 5000)
    Library(ap, s).scan()
    snd = Sender(ap, s, limit_bytes=1000)
    cp = snd.cache_path("a/😂 big meme.mp4")
    assert cp.name == "😂 big meme.mp4"
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_bytes(b"y")
    (ap.send / "123-456-1000.part.mp4").write_bytes(b"in progress")
    (ap.send / "old").mkdir(); (ap.send / "old" / "z.mp4").write_bytes(b"z")
    snd.prune()
    assert cp.exists() and (ap.send / "123-456-1000.part.mp4").exists() and not (ap.send / "old").exists()


@needs_ff
def test_make_compatible_sees_rotated_hevc(tmp_path):
    from reelgrab.downloader import make_compatible, probe_codecs
    src = _make_clip(tmp_path / "src.mp4", seconds=1, vcodec="libx265")
    rot = tmp_path / "rot.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-display_rotation", "90", "-i", str(src), "-c", "copy", str(rot)],
                   check=True)
    assert probe_codecs(str(rot))["video"] == "hevc"
    assert make_compatible(str(rot)) is True and probe_codecs(str(rot))["video"] == "h264"


@pytest.mark.skipif(sys.platform != "win32", reason="windows console flag")
def test_downloader_ffmpeg_calls_hide_console(monkeypatch, tmp_path):
    import reelgrab.downloader as d
    calls = []
    class R:
        stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac"}]}'
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: calls.append(k) or R())
    monkeypatch.setattr(d, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(d, "ffprobe_path", lambda: "ffprobe")
    d.make_compatible(str(tmp_path / "x.mp4"))
    d.mux("v", None, "o")
    assert calls and all(k.get("creationflags") == subprocess.CREATE_NO_WINDOW for k in calls)


def test_download_writes_part_then_final(monkeypatch, tmp_path):
    import reelgrab.downloader as d
    from reelgrab.models import Media, VideoItem, VideoVariant
    final = tmp_path / "someone_ABC.mp4"
    def fake_fetch(client, url, dest, progress=None, headers=None):
        assert not final.exists() and dest.endswith(".part.mp4")
        Path(dest).write_bytes(b"data")
    monkeypatch.setattr(d, "fetch", fake_fetch)
    monkeypatch.setattr(d, "make_compatible", lambda p: False)
    media_ = Media("ABC", "someone", videos=[VideoItem(variants=[VideoVariant("https://x/v.mp4", 1, 1)])])
    assert d.download(media_, None, str(tmp_path), "progressive") == [str(final)]
    assert final.read_bytes() == b"data" and not list(tmp_path.glob("*.part.mp4"))


def test_folder_with_subfolders_can_be_renamed_while_app_runs(qtbot, ap):
    from reelgrab.archive.app import build
    _touch(ap, "memes/cats/x.mp4")
    ctx = build(ap, limit_bytes=10 * 1024 * 1024, process=False)
    assert ctx.library.rename("memes", "reactions") == "reactions"
    ctx.shutdown()


def test_mutating_actions_stop_hover_preview(qtbot, ap, monkeypatch):
    from reelgrab.archive.app import build
    _touch(ap, "_inbox/x.mp4")
    ctx = build(ap, limit_bytes=10 * 1024 * 1024, process=False)
    stops = []
    monkeypatch.setattr(ctx.window.grid, "_stop_preview", lambda: stops.append(1))
    ctx.window._trash(["_inbox/x.mp4"])
    ctx.window._undo()
    assert len(stops) >= 2
    ctx.shutdown()
