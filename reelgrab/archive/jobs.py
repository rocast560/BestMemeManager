"""background work: making clips phone-compatible, probing, thumbnails, pre-shrinking and
reel downloads. results come back to the ui thread through qt signals."""

import threading
import traceback
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import media
from ..downloader import make_compatible
from .library import Library
from .paths import ArchivePaths
from .sending import Sender
from .store import Store


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        self.fn()


class Jobs(QObject):
    clip_processed = Signal(str)
    download_started = Signal(str, str)  # job id, url
    download_done = Signal(str, list)  # job id, new rels
    download_failed = Signal(str, str, object)  # job id, message, retry callable
    failed = Signal(str)  # message
    busy = Signal(int)

    def __init__(self, paths: ArchivePaths, store: Store, sender: Sender, library: Library, client_factory=None):
        super().__init__()
        self.paths, self.store, self.sender, self.library = paths, store, sender, library
        self._client_factory = client_factory
        self._client = None
        self._client_lock = threading.Lock()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        self._dl_pool = QThreadPool()
        self._dl_pool.setMaxThreadCount(1)
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._shrinking: dict[str, threading.Event] = {}
        self._count = 0

    # ---------- bookkeeping ----------

    def _bump(self, n: int) -> None:
        with self._lock:
            self._count += n
            c = self._count
        self.busy.emit(c)

    def thumb_path(self, rel: str) -> Path:
        c = self.store.get(rel)
        if c:
            size, mtime = c.size, c.mtime
        else:
            st = self.paths.abs(rel).stat()
            size, mtime = st.st_size, st.st_mtime
        return self.paths.thumbs / f"{size}-{int(mtime * 1_000_000)}.jpg"

    def shutdown(self) -> None:
        self._pool.clear()
        self._dl_pool.clear()
        self._pool.waitForDone(10000)
        self._dl_pool.waitForDone(10000)

    # ---------- clip processing ----------

    def process(self, rels: list[str]) -> None:
        for rel in rels:
            with self._lock:
                if rel in self._inflight:
                    continue
                self._inflight.add(rel)
                if self.sender.needs_shrink(rel) and not self.sender.cache_path(rel).exists():
                    self._shrinking.setdefault(rel, threading.Event())
            self._bump(1)
            self._pool.start(_Task(lambda r=rel: self._process_one(r)))

    def _process_one(self, rel: str) -> None:
        try:
            path = self.paths.abs(rel)
            if not path.exists() or not media.available():
                return
            if make_compatible(str(path)):
                self.library.scan()
            c = self.store.get(rel)
            if c and c.duration is None:
                info = media.probe(path)
                self.store.set_probe(rel, info.duration, info.width, info.height, info.vcodec)
            thumb = self.thumb_path(rel)
            if not thumb.exists():
                media.thumbnail(path, thumb)
            self.clip_processed.emit(rel)
            self.sender.prepare(rel)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(f"couldn't process {rel}: {e}")
        finally:
            with self._lock:
                self._inflight.discard(rel)
                ev = self._shrinking.pop(rel, None)
            if ev:
                ev.set()
            self._bump(-1)
            self.clip_processed.emit(rel)

    def wait_shrunk(self, rel: str, timeout: float = 3.0) -> bool:
        if not self.sender.needs_shrink(rel) or self.sender.cache_path(rel).exists():
            return True
        with self._lock:
            ev = self._shrinking.get(rel)
        if ev is None:
            self.process([rel])
            with self._lock:
                ev = self._shrinking.get(rel)
        if ev is not None:
            ev.wait(timeout)
        return self.sender.cache_path(rel).exists()

    # ---------- downloads ----------

    def _get_client(self):
        if self._client is None:
            if self._client_factory:
                self._client = self._client_factory()
            else:
                from ..client import IGClient
                self._client = IGClient()
        return self._client

    def download(self, url: str, dest_folder: str = ArchivePaths.INBOX) -> str:
        job_id = uuid.uuid4().hex
        self.download_started.emit(job_id, url)
        self._bump(1)

        def work():
            try:
                from ..downloader import download
                from ..extractor import extract
                with self._client_lock:
                    client = self._get_client()
                    found = extract(url, client)
                    paths = download(found, client, str(self.paths.abs(dest_folder)))
                self.library.scan()
                rels = [self.paths.rel(p) for p in paths]
                self.download_done.emit(job_id, rels)
                self.process(rels)
            except Exception as e:
                self.download_failed.emit(job_id, str(e) or type(e).__name__, lambda: self.download(url, dest_folder))
            finally:
                self._bump(-1)

        self._dl_pool.start(_Task(work))
        return job_id
