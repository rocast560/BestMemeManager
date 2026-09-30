"""left-hand folder tree: pinned views plus the real folders, and a drop target for moves/imports."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QMenu, QTreeWidget, QTreeWidgetItem

from ..paths import ArchivePaths
from ..sending import RELS_MIME

FAVORITES, RECENT, INBOX = "@favorites", "@recent", ArchivePaths.INBOX
PINNED = [(FAVORITES, "★  Favorites"), (RECENT, "🕘  Recent"), (INBOX, "📥  Inbox")]
KeyRole = Qt.UserRole + 1


class FolderTree(QTreeWidget):
    folder_selected = Signal(str)
    clips_dropped = Signal(list, str)  # rels, dest folder
    files_dropped = Signal(list, str)  # external paths, dest folder
    new_folder = Signal(str)  # parent folder ("" = root)
    rename_folder = Signal(str)
    delete_folder = Signal(str)
    open_folder = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.currentItemChanged.connect(lambda cur, _: cur and self.folder_selected.emit(cur.data(0, KeyRole)))
        self.folder_items: dict[str, QTreeWidgetItem] = {}
        self._pinned: dict[str, QTreeWidgetItem] = {}

    def refresh(self, folders: list[str]) -> None:
        current = self.current_key()
        expanded = {k for k, it in self.folder_items.items() if it.isExpanded()}
        self.blockSignals(True)
        self.clear()
        self.folder_items.clear()
        self._pinned.clear()
        for key, label in PINNED:
            it = QTreeWidgetItem([label])
            it.setData(0, KeyRole, key)
            self.addTopLevelItem(it)
            self._pinned[key] = it
        for rel in folders:
            if rel == INBOX or rel.startswith(INBOX + "/"):
                continue
            parent_rel, _, name = rel.rpartition("/")
            it = QTreeWidgetItem(["📁  " + name])
            it.setData(0, KeyRole, rel)
            parent = self.folder_items.get(parent_rel)
            if parent is not None:
                parent.addChild(it)
            else:
                self.addTopLevelItem(it)
            self.folder_items[rel] = it
        for k in expanded:
            if k in self.folder_items:
                self.folder_items[k].setExpanded(True)
        self.blockSignals(False)
        self.select(current if current in self.folder_items or current in self._pinned else INBOX, emit=False)

    def current_key(self) -> str:
        it = self.currentItem()
        return it.data(0, KeyRole) if it else INBOX

    def select(self, key: str, emit: bool = True) -> None:
        it = self.folder_items.get(key) or self._pinned.get(key)
        if it is None:
            return
        self.blockSignals(not emit)
        self.setCurrentItem(it)
        p = it.parent()
        while p:
            p.setExpanded(True)
            p = p.parent()
        self.blockSignals(False)

    # ---------- drops ----------

    def _target(self, pos) -> str | None:
        it = self.itemAt(pos)
        if it is None:
            return None
        key = it.data(0, KeyRole)
        return INBOX if key in (FAVORITES, RECENT) else key

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(RELS_MIME) or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        pos = e.position().toPoint()
        it = self.itemAt(pos)
        internal = e.mimeData().hasFormat(RELS_MIME)
        if it is None or (internal and it.data(0, KeyRole) in (FAVORITES, RECENT)):
            e.ignore()
            return
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dropEvent(self, e):
        dest = self._target(e.position().toPoint())
        if dest is None:
            e.ignore()
            return
        md = e.mimeData()
        if md.hasFormat(RELS_MIME):
            rels = bytes(md.data(RELS_MIME)).decode("utf-8").split("\n")
            self.clips_dropped.emit([r for r in rels if r], dest)
        else:
            files = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
            files = [f for f in files if f.suffix.lower() == ".mp4" and f.is_file()]
            if files:
                self.files_dropped.emit(files, dest)
        e.setDropAction(Qt.CopyAction)
        e.accept()

    # ---------- context menu ----------

    def _menu(self, pos):
        it = self.itemAt(pos)
        key = it.data(0, KeyRole) if it else ""
        real = key not in (FAVORITES, RECENT)
        m = QMenu(self)
        if real:
            m.addAction("New folder", lambda: self.new_folder.emit("" if key == INBOX else key))
        if key and key not in (FAVORITES, RECENT, INBOX):
            m.addAction("Rename", lambda: self.rename_folder.emit(key))
            m.addAction("Delete", lambda: self.delete_folder.emit(key))
        if real:
            m.addSeparator()
            m.addAction("Open in Explorer", lambda: self.open_folder.emit(key))
        m.exec(self.viewport().mapToGlobal(pos))
