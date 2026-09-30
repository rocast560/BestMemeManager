"""the main archive window: link box, search, folder tree, clip grid, downloads strip."""

import os
import subprocess

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import media
from .grid import ClipGrid
from .icons import app_icon
from .tree import FAVORITES, INBOX, RECENT, FolderTree

STYLE = """
QMainWindow, QWidget { background: #151518; color: #e6e6e6; }
QLineEdit { background: #1e1e22; border: 1px solid #333; border-radius: 6px; padding: 6px 8px; }
QLineEdit:focus { border-color: #8b5cf6; }
QPushButton { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #34343c; }
QPushButton#primary { background: #7c3aed; border-color: #7c3aed; }
QPushButton#primary:hover { background: #8b5cf6; }
QTreeWidget, QListView, QListWidget { background: #151518; border: none; }
QTreeWidget::item { padding: 4px 2px; }
QTreeWidget::item:selected { background: #2d2640; color: white; }
QLabel#banner { background: #7f1d1d; color: white; padding: 6px 10px; border-radius: 6px; }
QStatusBar { color: #aaa; }
QMenu { background: #1e1e22; border: 1px solid #333; }
QMenu::item:selected { background: #2d2640; }
"""


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("BestMemeManager")
        self.setWindowIcon(app_icon())
        self.resize(1180, 760)
        self.setStyleSheet(STYLE)

        # top bar
        self.link = QLineEdit(placeholderText="Paste an Instagram reel link and press Enter…")
        self.link.returnPressed.connect(self._download)
        dl = QPushButton("Download", objectName="primary", clicked=self._download)
        self.search = QLineEdit(placeholderText="🔍  Search name, tag or folder")
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self, singleShot=True, interval=150, timeout=self.refresh_grid)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        gear = QPushButton("⚙", clicked=lambda: ctx.open_settings())
        gear.setToolTip("Settings")
        top = QHBoxLayout()
        top.addWidget(self.link, 3)
        top.addWidget(dl)
        top.addSpacing(12)
        top.addWidget(self.search, 2)
        top.addWidget(gear)

        self.banner = QLabel("ffmpeg not found – thumbnails, shrinking and mobile conversion are disabled. "
                             "Install it (winget install Gyan.FFmpeg) and restart.", objectName="banner")
        self.banner.setVisible(not media.available())

        self.downloads = QListWidget()
        self.downloads.setMaximumHeight(90)
        self.downloads.hide()
        self._dl_items: dict[str, QListWidgetItem] = {}

        self.tree = FolderTree()
        self.grid = ClipGrid(ctx)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(self.grid)
        split.setSizes([240, 940])

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(10, 10, 10, 4)
        lay.addLayout(top)
        lay.addWidget(self.banner)
        lay.addWidget(self.downloads)
        lay.addWidget(split, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Select clips and Ctrl+C, or drag them into Discord")

        self._wire()
        self._actions()

    # ---------- wiring ----------

    def _wire(self):
        c = self.ctx
        self.tree.folder_selected.connect(lambda _: self.refresh_grid())
        self.tree.clips_dropped.connect(self._move_to)
        self.tree.files_dropped.connect(self._import_to)
        self.tree.new_folder.connect(self._new_folder)
        self.tree.rename_folder.connect(self._rename)
        self.tree.delete_folder.connect(lambda rel: self._trash([rel]))
        self.tree.open_folder.connect(lambda rel: os.startfile(c.paths.abs(rel)))
        self.grid.files_dropped.connect(lambda files: self._import_to(files, self._drop_folder()))
        self.grid.model().warn.connect(self.notify)
        self.grid.doubleClicked.connect(lambda idx: os.startfile(c.paths.abs(self.grid.model().rel_at(idx.row()))))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._grid_menu)
        c.jobs.clip_processed.connect(self.grid.model().refresh_rel)
        c.jobs.download_started.connect(self._dl_started)
        c.jobs.download_done.connect(self._dl_done)
        c.jobs.download_failed.connect(self._dl_failed)
        c.jobs.failed.connect(self.notify)
        c.jobs.busy.connect(lambda n: self.statusBar().showMessage(f"working on {n} clip(s)…" if n else "ready", 0 if n else 3000))

    def _actions(self):
        def act(text, keys, fn, target=None):
            a = QAction(text, target or self.grid)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.triggered.connect(fn)
            (target or self.grid).addAction(a)
            return a
        self.a_copy = act("Copy for Discord", ["Ctrl+C"], self.copy_selected)
        self.a_open = act("Open", ["Return", "Enter"], self._open)
        self.a_show = act("Show in Explorer", [], self._show_in_explorer)
        self.a_fav = act("Favorite", ["F"], self._toggle_fav)
        self.a_tags = act("Tags…", ["T"], self._edit_tags)
        self.a_rename = act("Rename", ["F2"], lambda: self._rename(self._one()))
        self.a_delete = act("Delete", ["Del"], lambda: self._trash(self.grid.selected_rels()))
        undo = QAction("Undo", self, shortcut=QKeySequence("Ctrl+Z"), triggered=self._undo)
        undo.setShortcutContext(Qt.WindowShortcut)
        self.addAction(undo)
        find = QAction("Find", self, shortcut=QKeySequence("Ctrl+F"), triggered=self.search.setFocus)
        self.addAction(find)

    # ---------- views ----------

    def show_folder(self, key: str) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.tree.select(key, emit=False)
        self.refresh_grid()

    def refresh_grid(self) -> None:
        s = self.ctx.store
        text = self.search.text().strip()
        key = self.tree.current_key()
        if text:
            rels = [c.rel_path for c in s.search(text)]
        elif key == FAVORITES:
            rels = [c.rel_path for c in s.favorites()]
        elif key == RECENT:
            rels = [c.rel_path for c in s.recent()]
        else:
            rels = self.ctx.library.clips_in(key)
        self.grid.model().set_rels(rels)

    def rescan(self) -> None:
        self.ctx.library.scan()  # new clips reach jobs.process through library.on_added
        self.tree.refresh(self.ctx.library.folders())
        selected = set(self.grid.selected_rels())
        self.refresh_grid()
        m = self.grid.model()
        for row, rel in enumerate(m.rels):
            if rel in selected:
                self.grid.selectionModel().select(m.index(row), QItemSelectionModel.Select)

    def notify(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 6000)

    # ---------- clip actions ----------

    def _one(self) -> str | None:
        rels = self.grid.selected_rels()
        return rels[0] if rels else None

    def copy_selected(self) -> None:
        self.ctx.actions.copy(self.grid.selected_rels())

    def on_clips_changed(self, rels: list) -> None:
        if self.tree.current_key() in (FAVORITES, RECENT) and not self.search.text().strip():
            self.refresh_grid()
        else:
            for r in rels:
                self.grid.model().refresh_rel(r)

    def _open(self):
        rel = self._one()
        if rel:
            os.startfile(self.ctx.paths.abs(rel))

    def _show_in_explorer(self):
        rel = self._one()
        if rel:
            subprocess.Popen(["explorer", "/select,", str(self.ctx.paths.abs(rel))])

    def _toggle_fav(self):
        self.ctx.actions.toggle_favorite(self.grid.selected_rels())

    def _edit_tags(self):
        self.ctx.actions.edit_tags(self.grid.selected_rels(), self)

    def _rename(self, rel: str | None):
        if not rel:
            return
        self.grid._stop_preview()
        old = rel.rpartition("/")[2]
        is_clip = old.lower().endswith(".mp4")
        shown = old[:-4] if is_clip else old
        text, ok = QInputDialog.getText(self, "Rename", "New name:", text=shown)
        if not ok or not text.strip() or text.strip() == shown:
            return
        try:
            new = self.ctx.library.rename(rel, text)
        except (ValueError, OSError) as e:
            self.notify(f"Rename failed: {e}")
            return
        self.rescan()
        if not is_clip:
            self.tree.select(new, emit=False)
            self.refresh_grid()

    def _trash(self, rels: list[str]):
        self.ctx.actions.trash(rels)

    def _undo(self):
        self.ctx.actions.undo()

    def _move_to(self, rels: list[str], dest: str):
        self.grid.internal_drop = True
        self.ctx.actions.move(rels, dest)

    def _drop_folder(self) -> str:
        key = self.tree.current_key()
        return INBOX if key in (FAVORITES, RECENT) else key

    def _import_to(self, files, dest: str):
        try:
            rels = self.ctx.library.import_files(files, dest)
        except OSError as e:
            self.notify(f"Import failed: {e}")
            return
        self.rescan()
        self.ctx.jobs.process(rels)
        self.notify(f"Imported {len(rels)} clip(s)")

    def _new_folder(self, parent: str):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok or not name.strip():
            return
        try:
            rel = self.ctx.library.mkdir(parent, name)
        except (ValueError, OSError) as e:
            self.notify(f"Couldn't create folder: {e}")
            return
        self.rescan()
        self.tree.select(rel)

    def _grid_menu(self, pos):
        if not self.grid.indexAt(pos).isValid():
            return
        m = QMenu(self)
        for a in (self.a_copy, self.a_open, self.a_show, None, self.a_fav, self.a_tags, self.a_rename, self.a_delete):
            m.addSeparator() if a is None else m.addAction(a)
        move = m.addMenu("Move to")
        for rel in [INBOX] + [f for f in self.ctx.library.folders() if f != INBOX]:
            move.addAction(rel, lambda r=rel: self._move_to(self.grid.selected_rels(), r))
        m.exec(self.grid.viewport().mapToGlobal(pos))

    # ---------- downloads ----------

    def _download(self):
        url = self.link.text().strip()
        if not url:
            return
        self.link.clear()
        self.ctx.jobs.download(url)

    def _dl_row(self, job_id: str, text: str, retry=None):
        it = self._dl_items.get(job_id)
        if it is None:
            it = QListWidgetItem()
            self.downloads.insertItem(0, it)
            self._dl_items[job_id] = it
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(6, 2, 6, 2)
        row.addWidget(QLabel(text), 1)
        if retry:
            def do_retry():
                self._remove_dl(job_id)
                retry()
            row.addWidget(QPushButton("Retry", clicked=do_retry))
            row.addWidget(QPushButton("✕", clicked=lambda: self._remove_dl(job_id)))
        it.setSizeHint(w.sizeHint())
        self.downloads.setItemWidget(it, w)
        self.downloads.show()

    def _remove_dl(self, job_id: str):
        it = self._dl_items.pop(job_id, None)
        if it is not None:
            self.downloads.takeItem(self.downloads.row(it))
        self.downloads.setVisible(bool(self._dl_items))

    def _dl_started(self, job_id: str, url: str):
        self._dl_row(job_id, f"⏳  Downloading {url}")

    def _dl_done(self, job_id: str, rels: list):
        self._dl_row(job_id, f"✅  Saved {len(rels)} clip(s) to Inbox")
        QTimer.singleShot(5000, lambda: self._remove_dl(job_id))
        self.rescan()

    def _dl_failed(self, job_id: str, msg: str, retry):
        self._dl_row(job_id, "❌  " + msg.strip().splitlines()[0][:200], retry)

    # ---------- window ----------

    def closeEvent(self, e):
        if self.ctx.close_to_tray():
            e.ignore()
            self.hide()
        else:
            super().closeEvent(e)
