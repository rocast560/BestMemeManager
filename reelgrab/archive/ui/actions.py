"""clip actions shared by the main window and the tray flyout, so both behave the same."""

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QInputDialog, QMenu

from ..paths import ArchivePaths
from ..sending import copy_to_clipboard


class ClipActions(QObject):
    notify = Signal(str)
    files_changed = Signal()  # something moved on disk: views should rescan
    clips_changed = Signal(list)  # metadata (favorite/tags) changed for these rels

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.before_file_op: list = []  # e.g. stop hover previews, which hold files open on windows

    def _release(self) -> None:
        for fn in self.before_file_op:
            fn()

    # ---------- sending ----------

    def copy(self, rels: list[str]) -> None:
        if not rels:
            return
        paths, warns = [], []
        for rel in rels:
            self.ctx.jobs.wait_shrunk(rel)
            p, w = self.ctx.sender.send_path(rel)
            paths.append(p)
            if w:
                warns.append(w)
        copy_to_clipboard(paths)
        self.ctx.sender.log(rels)
        self.clips_changed.emit(rels)
        self.notify.emit(warns[0] if warns else f"Copied {len(rels)} clip(s) – paste in Discord (Ctrl+V)")

    # ---------- metadata ----------

    def toggle_favorite(self, rels: list[str]) -> None:
        if not rels:
            return
        s = self.ctx.store
        fav = not all((s.get(r) and s.get(r).favorite) for r in rels)
        for r in rels:
            s.set_favorite(r, fav)
        self.clips_changed.emit(rels)

    def edit_tags(self, rels: list[str], parent=None) -> None:
        if not rels:
            return
        c = self.ctx.store.get(rels[0])
        text, ok = QInputDialog.getText(parent, "Tags", "Comma-separated tags:", text=", ".join(c.tags) if c else "")
        if ok:
            for r in rels:
                self.ctx.store.set_tags(r, text.split(","))
            self.clips_changed.emit(rels)

    # ---------- files ----------

    def move(self, rels: list[str], dest: str) -> None:
        if not rels:
            return
        self._release()
        try:
            self.ctx.library.move(rels, dest)
        except (ValueError, OSError) as e:
            self.notify.emit(f"Move failed: {e}")
            return
        self.files_changed.emit()
        self.notify.emit(f"Moved {len(rels)} clip(s) to {dest} – Ctrl+Z to undo")

    def trash(self, rels: list[str]) -> None:
        rels = [r for r in rels if r and r != ArchivePaths.INBOX]
        if not rels:
            return
        self._release()
        try:
            self.ctx.library.trash(rels)
        except OSError as e:
            self.notify.emit(f"Delete failed: {e}")
            return
        self.files_changed.emit()
        self.notify.emit(f"Moved {len(rels)} item(s) to trash – Ctrl+Z to undo")

    def undo(self) -> None:
        self._release()
        if self.ctx.library.undo():
            self.files_changed.emit()
            self.notify.emit("Undone")
        else:
            self.notify.emit("Nothing to undo")

    # ---------- menu ----------

    def menu(self, rels: list[str], parent) -> QMenu:
        m = QMenu(parent)
        m.addAction("Copy for Discord", lambda: self.copy(rels))
        s = self.ctx.store
        fav = all((s.get(r) and s.get(r).favorite) for r in rels)
        m.addAction("Unfavorite" if fav else "★ Favorite", lambda: self.toggle_favorite(rels))
        m.addAction("Tags…", lambda: self.edit_tags(rels, parent))
        move = m.addMenu("Move to")
        for rel in [ArchivePaths.INBOX] + [f for f in self.ctx.library.folders() if f != ArchivePaths.INBOX]:
            move.addAction(rel, lambda r=rel: self.move(rels, r))
        m.addSeparator()
        m.addAction("Delete", lambda: self.trash(rels))
        return m
