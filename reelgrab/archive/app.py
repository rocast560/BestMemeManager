"""entry point for the reelgrab archive desktop app (windows)."""

import getpass
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import win32
from .hotkeyspec import parse_hotkey
from .jobs import Jobs
from .library import Library
from .paths import ArchivePaths, default_root
from .sending import Sender
from .settings import Settings
from .store import Store
from .ui.icons import app_icon
from .ui.main_window import MainWindow
from .ui.picker import Picker

HOTKEY_ID = 0xB00B
REPO = Path(__file__).resolve().parents[2]


class HotkeyWindow(QWidget):
    """hidden native window that owns the global hotkey registration."""

    def __init__(self, on_hotkey):
        super().__init__()
        self.on_hotkey = on_hotkey
        self.hwnd = int(self.winId())

    def nativeEvent(self, event_type, message):
        if win32.IS_WIN and event_type == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == win32.WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.on_hotkey(win32.foreground_window())
                return True, 0
        return False, 0


class Ctx:
    def __init__(self, paths: ArchivePaths, settings: Settings):
        self.paths = paths
        self.settings = settings
        self.store = Store(paths.db)
        self.library = Library(paths, self.store)
        self.sender = Sender(paths, self.store, settings.limit_bytes)
        self.jobs = Jobs(paths, self.store, self.sender, self.library)
        self.tray: QSystemTrayIcon | None = None
        self.hotkey_win: HotkeyWindow | None = None
        self._told_tray = False
        self.window = MainWindow(self)
        self.picker = Picker(self)
        # poll instead of QFileSystemWatcher: on windows a watched subfolder holds a handle that
        # blocks renaming/moving its parent, both in the app and in explorer
        self._fs_sig = None
        self._poll = QTimer(interval=2000, timeout=self._poll_fs)

    # ---------- used by the ui ----------

    def notify(self, msg: str) -> None:
        if self.window.isVisible():
            self.window.notify(msg)
        elif self.tray:
            self.tray.showMessage("reelgrab", msg, app_icon(), 4000)

    def close_to_tray(self) -> bool:
        if not (self.tray and self.tray.isVisible()):
            return False
        if not self._told_tray:
            self._told_tray = True
            self.tray.showMessage("reelgrab archive", f"Still running – press {self.settings.hotkey} to pick a meme.",
                                  app_icon(), 4000)
        return True

    def open_settings(self, focus_hotkey: bool = False) -> None:
        from .ui.settings_dialog import SettingsDialog
        SettingsDialog(self, self.window if self.window.isVisible() else None, focus_hotkey).exec()

    def apply_settings(self) -> None:
        self.sender.limit_bytes = self.settings.limit_bytes
        self.register_hotkey()
        self.jobs.process([c.rel_path for c in self.store.all()])
        self.window.grid.viewport().update()

    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    # ---------- hotkey / watcher ----------

    def register_hotkey(self) -> bool:
        if self.hotkey_win is None:
            self.hotkey_win = HotkeyWindow(lambda prev: self.picker.popup(prev))
        win32.unregister_hotkey(self.hotkey_win.hwnd, HOTKEY_ID)
        try:
            mods, vk = parse_hotkey(self.settings.hotkey)
        except ValueError:
            return False
        return win32.register_hotkey(self.hotkey_win.hwnd, HOTKEY_ID, mods, vk)

    def _fs_signature(self) -> int:
        entries = []
        for dirpath, dirnames, filenames in os.walk(self.paths.root):
            if Path(dirpath) == self.paths.root:
                dirnames[:] = [d for d in dirnames if d != ArchivePaths.DATA]
            entries.append(dirpath)
            for f in filenames:
                try:
                    st = os.stat(os.path.join(dirpath, f))
                except OSError:
                    continue
                entries.append((dirpath, f, st.st_size, st.st_mtime_ns))
        return hash(tuple(sorted(map(str, entries))))

    def _poll_fs(self) -> None:
        sig = self._fs_signature()
        if self._fs_sig is not None and sig != self._fs_sig:
            self.window.rescan()
        self._fs_sig = sig

    def start_polling(self) -> None:
        self._fs_sig = self._fs_signature()
        self._poll.start()

    def shutdown(self) -> None:
        self._poll.stop()
        self.jobs.shutdown()
        if self.hotkey_win is not None:
            win32.unregister_hotkey(self.hotkey_win.hwnd, HOTKEY_ID)
        self.picker.close()
        self.window.hide()
        self.store.db.close()


def build(paths: ArchivePaths, limit_bytes: int | None = None, process: bool = True,
          settings: Settings | None = None) -> Ctx:
    settings = settings or Settings()
    if limit_bytes:
        settings.limit_mb = limit_bytes / 1024 / 1024
    paths.ensure()
    ctx = Ctx(paths, settings)
    ctx.library.scan()
    ctx.window.tree.refresh(ctx.library.folders())
    ctx.window.refresh_grid()
    if process:
        ctx.sender.prune()  # before workers start writing into send/
        ctx.library.on_added = ctx.jobs.process  # any scan, on any thread, queues new clips
        ctx.jobs.process([c.rel_path for c in ctx.store.all()])
        ctx.start_polling()
    return ctx


# ---------- desktop entry ----------

def _load_dotenv() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _log_to_file(paths: ArchivePaths) -> None:
    # pythonw has no console; keep tracebacks somewhere findable
    if sys.stderr is None or sys.stdout is None:
        f = open(paths.data / "archive.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _single_instance(name: str) -> QLocalServer | None:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return None
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)
    return server


def main() -> int:
    if win32.IS_WIN:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("reelgrab.archive")
    _load_dotenv()
    app = QApplication(sys.argv)
    app.setApplicationName("reelgrab archive")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    server = _single_instance(f"reelgrab-archive-{getpass.getuser()}")
    if server is None:
        return 0

    settings = Settings.load()
    paths = ArchivePaths(default_root(settings.archive_dir))
    paths.ensure()
    _log_to_file(paths)
    ctx = build(paths, settings=settings)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (conn.readAll(), ctx.show_window()))
    server.newConnection.connect(on_connection)

    tray = QSystemTrayIcon(app_icon())
    tray.setToolTip(f"reelgrab archive – {settings.hotkey} to pick a meme")
    menu = QMenu()
    menu.addAction("Open", ctx.show_window)
    menu.addAction("Pick a meme…", lambda: ctx.picker.popup(0))
    menu.addAction("Settings…", ctx.open_settings)
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
                           and ctx.show_window())
    tray.show()
    ctx.tray = tray

    if "--tray" not in sys.argv[1:]:  # launch-at-login starts hidden in the tray
        ctx.window.show()
    if not ctx.register_hotkey():
        tray.showMessage("reelgrab archive", f"Couldn't register {settings.hotkey} – another app is using it. "
                         "Pick a different hotkey.", app_icon(), 6000)
        QTimer.singleShot(500, lambda: ctx.open_settings(focus_hotkey=True))

    code = app.exec()
    ctx.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
