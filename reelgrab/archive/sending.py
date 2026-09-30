"""decides which file actually goes to discord, and hands it over as a real file.

clips over the size limit get a shrunk copy in .reelgrab/send/<size-mtime-limit>/<name>.mp4,
keyed by size+mtime rather than folder so moving a clip doesn't throw the copy away.
"""

import shutil
from pathlib import Path

from . import media
from .paths import ArchivePaths
from .store import Store

RELS_MIME = "application/x-reelgrab-rels"


class Sender:
    def __init__(self, paths: ArchivePaths, store: Store, limit_bytes: int):
        self.paths = paths
        self.store = store
        self.limit_bytes = limit_bytes

    def _identity(self, rel: str) -> tuple[int, float]:
        c = self.store.get(rel)
        if c:
            return c.size, c.mtime
        st = self.paths.abs(rel).stat()
        return st.st_size, st.st_mtime

    def cache_key(self, rel: str) -> str:
        size, mtime = self._identity(rel)
        return f"{size}-{int(mtime * 1_000_000)}-{self.limit_bytes}"

    def cache_path(self, rel: str) -> Path:
        # keep the clip's own filename: it's what discord shows the people you send it to
        return self.paths.send / self.cache_key(rel) / rel.rpartition("/")[2]

    def needs_shrink(self, rel: str) -> bool:
        return self._identity(rel)[0] > self.limit_bytes

    def send_path(self, rel: str) -> tuple[Path, str | None]:
        original = self.paths.abs(rel)
        if not self.needs_shrink(rel):
            return original, None
        cached = self.cache_path(rel)
        mb = f"{self.limit_bytes / 1024 / 1024:g}"
        if not cached.exists():
            return original, f"{original.name} is over {mb} MB and isn't shrunk yet - Discord may reject it"
        if cached.with_suffix(".lowq").exists():
            return cached, f"{original.name} is long; the shrunk copy is low quality"
        return cached, None

    def prepare(self, rel: str) -> Path:
        """blocking. makes the shrunk copy if one is needed and missing."""
        if not self.needs_shrink(rel):
            return self.paths.abs(rel)
        cached = self.cache_path(rel)
        if not cached.exists():
            r = media.shrink(self.paths.abs(rel), cached, self.limit_bytes)
            if r.low_quality:
                cached.with_suffix(".lowq").touch()
        return cached

    def prune(self) -> None:
        keep = set()
        for c in self.store.all():
            try:
                keep.add(self.cache_key(c.rel_path))
            except OSError:
                pass
        for f in self.paths.send.iterdir():
            if f.name in keep or ".part" in f.name:
                continue
            try:
                shutil.rmtree(f) if f.is_dir() else f.unlink()
            except OSError:  # in use by a running shrink or a drag in progress; next prune gets it
                pass

    def log(self, rels: list[str]) -> None:
        for rel in rels:
            self.store.log_send(rel)


def make_mime(paths: list[Path], rels: list[str] | None = None):
    from PySide6.QtCore import QMimeData, QUrl
    mime = QMimeData()
    # qt turns file urls into CF_HDROP on windows, the same thing explorer puts on the clipboard
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    if rels:
        mime.setData(RELS_MIME, "\n".join(rels).encode("utf-8"))
    return mime


def copy_to_clipboard(paths: list[Path]) -> None:
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().setMimeData(make_mime(paths))
