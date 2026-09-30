"""thumbnail grid: model, tile painter, hover preview and drag-out."""

from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from ..sending import RELS_MIME, make_mime
from ..store import Clip

TILE_W, TILE_H = 180, 330
IMG_H = 260
ClipRole = Qt.UserRole + 1


def fmt_duration(sec: float | None) -> str:
    if not sec:
        return ""
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


class ClipModel(QAbstractListModel):
    warn = Signal(str)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.rels: list[str] = []
        self._clips: dict[str, Clip] = {}
        self._pix: dict[str, QPixmap] = {}

    def set_rels(self, rels: list[str]) -> None:
        self.beginResetModel()
        self.rels = list(rels)
        self._clips.clear()
        self.endResetModel()

    def rel_at(self, row: int) -> str:
        return self.rels[row]

    def clip(self, rel: str) -> Clip:
        c = self._clips.get(rel)
        if c is None:
            c = self.ctx.store.get(rel)
            if c is None:
                try:
                    st = self.ctx.paths.abs(rel).stat()
                    c = Clip(rel, st.st_size, st.st_mtime)
                except OSError:
                    c = Clip(rel, 0, 0)
            self._clips[rel] = c
        return c

    def pixmap(self, rel: str) -> QPixmap | None:
        try:
            key = str(self.ctx.jobs.thumb_path(rel))
        except OSError:
            return None
        pm = self._pix.get(key)
        if pm is None and Path(key).exists():
            pm = QPixmap(key)
            if not pm.isNull():
                pm = pm.scaled(TILE_W - 12, IMG_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._pix[key] = pm
        return pm

    def refresh_rel(self, rel: str) -> None:
        if rel in self.rels:
            self._clips.pop(rel, None)
            row = self.rels.index(rel)
            self.dataChanged.emit(self.index(row), self.index(row))

    # ---------- qt model api ----------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rels)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        rel = self.rels[index.row()]
        if role == Qt.DisplayRole:
            return self.clip(rel).name
        if role == ClipRole:
            return self.clip(rel)
        if role == Qt.ToolTipRole:
            c = self.clip(rel)
            tags = f"\ntags: {', '.join(c.tags)}" if c.tags else ""
            return f"{rel}\n{fmt_size(c.size)}{tags}"
        return None

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemIsDragEnabled if index.isValid() else base

    def mimeTypes(self):
        return ["text/uri-list", RELS_MIME]

    def mimeData(self, indexes):
        rels = [self.rels[i.row()] for i in indexes if i.isValid()]
        paths, warns = [], []
        for rel in rels:
            p, w = self.ctx.sender.send_path(rel)
            paths.append(p)
            if w:
                warns.append(w)
        if warns:
            self.warn.emit(warns[0])
        return make_mime(paths, rels)

    def supportedDragActions(self):
        # copy only: a move-drop onto explorer would otherwise take the file out of the archive
        return Qt.CopyAction


class ClipDelegate(QStyledItemDelegate):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx

    def sizeHint(self, option, index):
        return QSize(TILE_W, TILE_H)

    def paint(self, p: QPainter, option, index):
        model: ClipModel = index.model()
        rel = model.rel_at(index.row())
        clip: Clip = index.data(ClipRole)
        r = option.rect.adjusted(4, 4, -4, -4)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        selected = option.state & QStyle.State_Selected
        hover = option.state & QStyle.State_MouseOver
        p.setPen(QPen(QColor("#8b5cf6"), 2) if selected else Qt.NoPen)
        p.setBrush(QColor("#2d2640") if selected else QColor("#26262b") if hover else QColor("#1e1e22"))
        p.drawRoundedRect(r, 8, 8)

        img = QRect(r.left() + 2, r.top() + 2, r.width() - 4, IMG_H)
        p.setBrush(QColor("#000"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(img, 6, 6)
        pm = model.pixmap(rel)
        if pm:
            x = img.left() + (img.width() - pm.width()) // 2
            y = img.top() + (img.height() - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        else:
            p.setPen(QColor("#666"))
            p.drawText(img, Qt.AlignCenter, "…")

        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        p.setFont(small)
        fm = QFontMetrics(small)

        def badge(text, rect_fn, bg):
            w = fm.horizontalAdvance(text) + 10
            br = rect_fn(w, fm.height() + 4)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(br, 4, 4)
            p.setPen(QColor("white"))
            p.drawText(br, Qt.AlignCenter, text)

        dur = fmt_duration(clip.duration)
        if dur:
            badge(dur, lambda w, h: QRect(img.left() + 6, img.bottom() - h - 6, w, h), QColor(0, 0, 0, 170))
        big = clip.size > self.ctx.sender.limit_bytes
        badge(fmt_size(clip.size), lambda w, h: QRect(img.right() - w - 6, img.bottom() - h - 6, w, h),
              QColor("#d97706") if big else QColor(0, 0, 0, 170))
        if clip.favorite:
            badge("★", lambda w, h: QRect(img.right() - w - 6, img.top() + 6, w, h), QColor(0, 0, 0, 170))

        p.setFont(option.font)
        p.setPen(QColor("#eee"))
        name_r = QRect(r.left() + 8, img.bottom() + 6, r.width() - 16, QFontMetrics(option.font).height())
        name = clip.name[:-4] if clip.name.lower().endswith(".mp4") else clip.name
        p.drawText(name_r, Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(option.font).elidedText(name, Qt.ElideRight, name_r.width()))

        p.setFont(small)
        x, y = r.left() + 8, name_r.bottom() + 6
        for tag in clip.tags[:3]:
            w = fm.horizontalAdvance(tag) + 10
            if x + w > r.right() - 6:
                break
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3b3552"))
            p.drawRoundedRect(QRect(x, y, w, fm.height() + 2), 6, 6)
            p.setPen(QColor("#d8ccff"))
            p.drawText(QRect(x, y, w, fm.height() + 2), Qt.AlignCenter, tag)
            x += w + 4
        p.restore()


class ClipGrid(QListView):
    files_dropped = Signal(list)  # external files dropped onto the grid

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.viewport().setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setItemDelegate(ClipDelegate(ctx, self))
        self.setModel(ClipModel(ctx))
        self.internal_drop = False  # set by the folder tree when a drag lands on it
        self._hover_row = -1
        self._hover_timer = QTimer(self, singleShot=True, interval=350, timeout=self._start_preview)
        self._player = None
        self._video = None
        self.verticalScrollBar().valueChanged.connect(lambda _: self._stop_preview())

    # ---------- hover preview ----------

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        idx = self.indexAt(e.position().toPoint())
        row = idx.row() if idx.isValid() else -1
        if row != self._hover_row:
            self._stop_preview()
            self._hover_row = row
            if row >= 0 and not e.buttons():
                self._hover_timer.start()

    def leaveEvent(self, e):
        self._stop_preview()
        self._hover_row = -1
        super().leaveEvent(e)

    def _ensure_player(self):
        if self._player is None:
            from PySide6.QtMultimedia import QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
            self._video = QVideoWidget(self.viewport())
            self._video.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._video.hide()
            self._player = QMediaPlayer(self)
            self._player.setVideoOutput(self._video)  # no audio output set, so previews are muted
            self._player.setLoops(QMediaPlayer.Infinite)

    def _start_preview(self):
        if self._hover_row < 0 or self._hover_row >= self.model().rowCount():
            return
        try:
            self._ensure_player()
        except Exception:
            return
        idx = self.model().index(self._hover_row)
        rect = self.visualRect(idx).adjusted(6, 6, -6, 0)
        rect.setHeight(IMG_H)
        self._video.setGeometry(rect)
        self._player.setSource(QUrl.fromLocalFile(str(self.ctx.paths.abs(self.model().rel_at(self._hover_row)))))
        self._video.show()
        self._player.play()

    def _stop_preview(self):
        self._hover_timer.stop()
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
            self._video.hide()

    # ---------- external drops (import) ----------

    def _external(self, e) -> bool:
        md = e.mimeData()
        return md.hasUrls() and not md.hasFormat(RELS_MIME)

    def dragEnterEvent(self, e):
        if self._external(e):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._external(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if self._external(e):
            files = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
            files = [f for f in files if f.suffix.lower() == ".mp4" and f.is_file()]
            if files:
                self.files_dropped.emit(files)
            e.acceptProposedAction()
        else:
            e.ignore()

    # ---------- drag out ----------

    def startDrag(self, supported):
        indexes = [i for i in self.selectionModel().selectedIndexes() if i.isValid()]
        if not indexes:
            return
        self._stop_preview()
        rels = [self.model().rel_at(i.row()) for i in indexes]
        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(indexes))
        pm = self.model().pixmap(rels[0])
        if pm:
            drag.setPixmap(pm.scaledToHeight(120, Qt.SmoothTransformation))
        self.internal_drop = False
        if drag.exec(Qt.CopyAction, Qt.CopyAction) == Qt.CopyAction and not self.internal_drop:
            self.ctx.sender.log(rels)  # dropped outside the app, most likely into discord

    def selected_rels(self) -> list[str]:
        rows = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        return [self.model().rel_at(r) for r in rows]
