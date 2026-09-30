"""compact tray window: click the tray icon, find a clip, click it, paste in discord."""

import time

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabBar,
                               QVBoxLayout, QWidget)

from .. import win32
from ..paths import ArchivePaths
from .grid import ClipGrid

W, H = 380, 560
TABS = [("favorites", "★ Favorites"), ("recent", "🕘 Recent"), ("folder", "📁 Folder")]

STYLE = """
QWidget#flyout { background: #17171b; border: 1px solid #3b3552; border-radius: 10px; }
QWidget { color: #e6e6e6; }
QLineEdit { background: #222228; border: 1px solid #333; border-radius: 6px; padding: 6px 8px; }
QLineEdit:focus { border-color: #8b5cf6; }
QComboBox { background: #222228; border: 1px solid #333; border-radius: 6px; padding: 4px 8px; }
QTabBar::tab { background: transparent; color: #aaa; padding: 6px 10px; border: none; }
QTabBar::tab:selected { color: white; border-bottom: 2px solid #8b5cf6; }
QListView { background: transparent; border: none; }
QPushButton { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { background: #34343c; }
QLabel#status { color: #a1a1aa; font-size: 11px; }
"""


def place(anchor: QRect | None, g: QRect) -> QRect:
    """where the panel goes: above (or below) the tray icon, always inside the screen area g."""
    h = min(H, g.height() - 8)
    valid = anchor is not None and anchor.isValid() and not anchor.isEmpty()
    if valid:
        x = anchor.center().x() - W // 2
        y = anchor.top() - h - 8 if anchor.center().y() > g.center().y() else anchor.bottom() + 8
    else:
        x, y = g.right() - W - 12, g.bottom() - h - 12
    x = max(g.left() + 4, min(x, g.right() - W - 4))
    y = max(g.top() + 4, min(y, g.bottom() - h - 4))
    return QRect(x, y, W, h)


class Flyout(QWidget):
    def __init__(self, ctx):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctx = ctx
        self.setObjectName("flyout")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setFixedSize(W, H)
        self._hidden_at = 0.0

        self.link = QLineEdit(placeholderText="Paste an Instagram or TikTok link…")
        self.link.returnPressed.connect(self._download)
        self.status = QLabel("Click a clip to copy it, or drag it into Discord", objectName="status")
        self.search = QLineEdit(placeholderText="🔍  Search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.tabs = QTabBar()
        for _, label in TABS:
            self.tabs.addTab(label)
        self.tabs.currentChanged.connect(lambda _: self._tab_changed())
        self.folder_box = QComboBox()
        self.folder_box.currentIndexChanged.connect(lambda _: self._folder_changed())
        self.grid = ClipGrid(ctx, compact=True)
        self.grid.clicked.connect(lambda idx: self.ctx.actions.copy([self.grid.model().rel_at(idx.row())]))
        self.grid.model().warn.connect(self.show_status)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        full = QPushButton("Open full window", clicked=self._open_full)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(self.link)
        lay.addWidget(self.status)
        lay.addWidget(self.search)
        lay.addWidget(self.tabs)
        lay.addWidget(self.folder_box)
        lay.addWidget(self.grid, 1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(full)
        lay.addLayout(foot)

        undo = QAction(self, shortcut=QKeySequence("Ctrl+Z"), triggered=lambda: self.ctx.actions.undo())
        self.addAction(undo)
        esc = QAction(self, shortcut=QKeySequence("Esc"), triggered=self.hide)
        self.addAction(esc)

        ctx.jobs.download_started.connect(lambda _id, url: self.show_status("⏳ Downloading…"))
        ctx.jobs.download_done.connect(self._dl_done)
        ctx.jobs.download_failed.connect(lambda _id, msg, _retry: self.show_status("❌ " + msg.strip().splitlines()[0][:120]))
        ctx.jobs.clip_processed.connect(self.grid.model().refresh_rel)

        self.reload_folders()
        self._tab_changed()

    # ---------- content ----------

    def current_tab(self) -> str:
        return TABS[max(0, self.tabs.currentIndex())][0]

    def set_tab(self, key: str) -> None:
        self.tabs.setCurrentIndex([k for k, _ in TABS].index(key))
        self._tab_changed()

    def reload_folders(self) -> None:
        want = self.folder_box.currentData() or self.ctx.settings.flyout_folder or ArchivePaths.INBOX
        self.folder_box.blockSignals(True)
        self.folder_box.clear()
        for rel in [ArchivePaths.INBOX] + [f for f in self.ctx.library.folders() if f != ArchivePaths.INBOX]:
            label = "📥  Inbox" if rel == ArchivePaths.INBOX else "    " * rel.count("/") + "📁  " + rel.rpartition("/")[2]
            self.folder_box.addItem(label, rel)
        i = self.folder_box.findData(want)
        self.folder_box.setCurrentIndex(i if i >= 0 else 0)
        self.folder_box.blockSignals(False)

    def refresh(self) -> None:
        s = self.ctx.store
        text = self.search.text().strip()
        tab = self.current_tab()
        if text:
            rels = [c.rel_path for c in s.search(text)]
        elif tab == "favorites":
            rels = [c.rel_path for c in s.favorites()]
        elif tab == "recent":
            rels = [c.rel_path for c in s.recent()]
        else:
            rels = self.ctx.library.clips_in(self.folder_box.currentData() or ArchivePaths.INBOX)
        self.grid.model().set_rels(rels)

    def files_changed(self) -> None:
        self.reload_folders()
        self.refresh()

    def clips_changed(self, rels: list) -> None:
        if self.current_tab() in ("favorites", "recent") and not self.search.text().strip():
            self.refresh()
        else:
            for r in rels:
                self.grid.model().refresh_rel(r)

    def show_status(self, msg: str) -> None:
        self.status.setText(msg)

    def _tab_changed(self) -> None:
        self.folder_box.setVisible(self.current_tab() == "folder")
        self.refresh()

    def _folder_changed(self) -> None:
        rel = self.folder_box.currentData()
        if rel and rel != self.ctx.settings.flyout_folder:
            self.ctx.settings.flyout_folder = rel
            try:
                self.ctx.settings.save()
            except OSError:
                pass
        self.refresh()

    def _menu(self, pos) -> None:
        idx = self.grid.indexAt(pos)
        if not idx.isValid():
            return
        if not self.grid.selectionModel().isSelected(idx):
            self.grid.setCurrentIndex(idx)
        rels = self.grid.selected_rels()
        self.ctx.actions.menu(rels, self).exec(self.grid.viewport().mapToGlobal(pos))

    def _download(self) -> None:
        url = self.link.text().strip()
        if url:
            self.link.clear()
            self.ctx.jobs.download(url)

    def _dl_done(self, _id: str, rels: list) -> None:
        self.show_status(f"✅ Saved {len(rels)} clip(s) to Inbox")
        self.files_changed()

    def _open_full(self) -> None:
        self.hide()
        self.ctx.show_window()

    # ---------- showing / hiding ----------

    def toggle(self, anchor: QRect | None) -> None:
        # clicking the tray icon while open first deactivates (hides) us; don't reopen on that click
        if self.isVisible() or time.monotonic() - self._hidden_at < 0.35:
            self.hide()
            return
        self.popup(anchor)

    def popup(self, anchor: QRect | None) -> None:
        valid = anchor is not None and anchor.isValid() and not anchor.isEmpty()
        screen = (QGuiApplication.screenAt(anchor.center()) if valid else None) \
            or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        r = place(anchor, screen.availableGeometry())
        self.setFixedSize(r.size())
        self.move(r.topLeft())
        self.reload_folders()
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()
        win32.focus_window(int(self.winId()))
        self.search.setFocus()

    def hideEvent(self, e):
        self._hidden_at = time.monotonic()
        self.grid._stop_preview()
        super().hideEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow():
            active = QApplication.activeWindow()
            busy = QApplication.activeModalWidget() or QApplication.activePopupWidget()
            if not busy and not (active is not None and active is not self and self.isAncestorOf(active)):
                self.hide()
        super().changeEvent(e)
