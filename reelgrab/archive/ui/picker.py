"""hotkey quick-picker: type, arrow, enter -> clip is pasted into whatever window you came from."""

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QLineEdit, QVBoxLayout, QWidget

from .. import win32
from ..sending import copy_to_clipboard

MAX_RESULTS = 8
RelRole = Qt.UserRole + 1

STYLE = """
QWidget#picker { background: #1b1b20; border: 1px solid #8b5cf6; border-radius: 10px; }
QLineEdit { background: #25252c; color: #eee; border: none; border-radius: 6px; padding: 8px 10px; font-size: 15px; }
QListWidget { background: transparent; color: #ddd; border: none; outline: none; }
QListWidget::item { padding: 4px; border-radius: 6px; }
QListWidget::item:selected { background: #2d2640; color: white; }
QLabel { color: #888; font-size: 11px; }
"""


class Picker(QWidget):
    def __init__(self, ctx):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctx = ctx
        self.prev_hwnd = 0
        self.setObjectName("picker")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(520)
        self.search = QLineEdit(placeholderText="Search memes…")
        self.search.textChanged.connect(lambda _: self.refresh())
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setIconSize(QSize(40, 64))
        self.list.itemActivated.connect(lambda _: self.send_current(paste=self.ctx.settings.auto_paste))
        self.hint = QLabel("Enter paste  ·  Shift+Enter copy only  ·  Esc close")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.addWidget(self.search)
        lay.addWidget(self.list)
        lay.addWidget(self.hint)

    def _rels(self) -> list[str]:
        s = self.ctx.store
        text = self.search.text().strip()
        if text:
            return [c.rel_path for c in s.search(text)][:MAX_RESULTS]
        seen, out = set(), []
        for c in s.favorites() + s.recent():
            if c.rel_path not in seen:
                seen.add(c.rel_path)
                out.append(c.rel_path)
        return out[:MAX_RESULTS]

    def refresh(self) -> None:
        self.list.clear()
        for rel in self._rels():
            folder, _, name = rel.rpartition("/")
            it = QListWidgetItem(f"{name[:-4] if name.lower().endswith('.mp4') else name}\n{folder or '/'}")
            it.setData(RelRole, rel)
            try:
                thumb = self.ctx.jobs.thumb_path(rel)
                if thumb.exists():
                    it.setIcon(QIcon(QPixmap(str(thumb))))
            except OSError:
                pass
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        rows = max(1, self.list.count())
        self.list.setFixedHeight(min(rows, MAX_RESULTS) * 74 + 4)
        self.adjustSize()

    def popup(self, prev_hwnd: int = 0) -> None:
        self.prev_hwnd = prev_hwnd
        self.search.clear()
        self.refresh()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.top() + g.height() // 4)
        self.show()
        self.raise_()
        self.activateWindow()
        win32.focus_window(int(self.winId()))
        self.search.setFocus()

    def send_current(self, paste: bool) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        rel = it.data(RelRole)
        self.ctx.jobs.wait_shrunk(rel)
        path, warn = self.ctx.sender.send_path(rel)
        copy_to_clipboard([path])
        self.ctx.sender.log([rel])
        self.hide()
        if warn:
            self.ctx.notify(warn)
        if paste and self.prev_hwnd:
            win32.focus_window(self.prev_hwnd)
            QTimer.singleShot(150, win32.send_ctrl_v)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.KeyPress:
            k = e.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                if 0 <= row < self.list.count():
                    self.list.setCurrentRow(row)
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                shift = bool(e.modifiers() & Qt.ShiftModifier)
                self.send_current(paste=self.ctx.settings.auto_paste and not shift)
                return True
            if k == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, e)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.hide()
        super().changeEvent(e)
