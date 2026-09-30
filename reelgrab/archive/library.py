"""filesystem side of the archive: folders, moves, renames, trash and undo.

every mutating op is a list of (src_rel, dst_rel) renames applied to both the disk and the
store, so undo is just replaying them backwards. trash is a move into .reelgrab/trash/.
"""

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .paths import ArchivePaths
from .store import Store

BAD_CHARS = set('/\\:*?"<>|')
VIDEO_EXT = ".mp4"


def is_clip_name(name: str) -> bool:
    n = name.lower()
    return n.endswith(VIDEO_EXT) and not n.endswith(".part.mp4") and not n.endswith(".tmp.mp4")


def unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = (dest.name, "") if dest.is_dir() else (dest.stem, dest.suffix)
    n = 2
    while True:
        cand = dest.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", "..") or BAD_CHARS & set(name):
        raise ValueError(f"invalid name: {name!r}")
    return name


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


class Library:
    def __init__(self, paths: ArchivePaths, store: Store):
        self.paths = paths
        self.store = store
        self._undo: list[list[tuple[str, str]]] = []
        self.lock = threading.RLock()  # scans run from worker threads too
        self.on_added = None  # called with new/changed rels from every scan, whichever thread ran it

    # ---------- reads ----------

    def _walk(self):
        for dirpath, dirnames, filenames in os.walk(self.paths.root):
            here = Path(dirpath)
            if here == self.paths.root:
                dirnames[:] = [d for d in dirnames if d != ArchivePaths.DATA]
            dirnames.sort(key=str.lower)
            yield here, dirnames, filenames

    def scan(self) -> tuple[list[str], list[str]]:
        with self.lock:
            added, removed = self._scan()
        if added and self.on_added:
            self.on_added(added)
        return added, removed

    def _scan(self) -> tuple[list[str], list[str]]:
        files = {}
        for here, _, filenames in self._walk():
            for f in filenames:
                if is_clip_name(f):
                    p = here / f
                    try:
                        st = p.stat()
                    except FileNotFoundError:  # vanished mid-walk
                        continue
                    files[self.paths.rel(p)] = (st.st_size, st.st_mtime)
        return self.store.sync(files)

    def folders(self) -> list[str]:
        out = []
        for here, dirnames, _ in self._walk():
            out += [self.paths.rel(here / d) for d in dirnames]
        return sorted(out, key=str.lower)

    def clips_in(self, folder: str) -> list[str]:
        def direct(d: Path) -> list[str]:
            if not d.is_dir():
                return []
            return sorted((self.paths.rel(p) for p in d.iterdir()
                           if p.is_file() and is_clip_name(p.name)), key=str.lower)
        out = direct(self.paths.abs(folder))
        if folder == ArchivePaths.INBOX:  # docker web ui saves loose files in the root
            out += direct(self.paths.root)
        return out

    # ---------- writes ----------

    def _apply(self, pairs: list[tuple[str, str]], record: bool = True) -> None:
        with self.lock:
            self._apply_locked(pairs, record)

    def _apply_locked(self, pairs: list[tuple[str, str]], record: bool) -> None:
        done = []
        try:
            for src, dst in pairs:
                d = self.paths.abs(dst)
                d.parent.mkdir(parents=True, exist_ok=True)
                os.replace(self.paths.abs(src), d)
                self.store.rename_prefix(src, dst)
                done.append((src, dst))
        finally:
            if record and done:
                self._undo.append(done)

    def mkdir(self, parent: str, name: str) -> str:
        p = unique_path(self.paths.abs(_join(parent, _check_name(name))))
        p.mkdir(parents=True)
        return self.paths.rel(p)

    def rename(self, rel: str, new_name: str) -> str:
        src = self.paths.abs(rel)
        name = _check_name(new_name)
        if src.is_file() and not name.lower().endswith(VIDEO_EXT):
            name += VIDEO_EXT
        if name == src.name:
            return rel
        dst = self.paths.rel(unique_path(src.with_name(name)))
        self._apply([(rel, dst)])
        return dst

    def move(self, rels: list[str], dest_folder: str) -> list[str]:
        dest_dir = self.paths.abs(dest_folder)
        pairs, out = [], []
        for rel in rels:
            src = self.paths.abs(rel)
            if src.parent == dest_dir:
                out.append(rel)
                continue
            if dest_folder == rel or dest_folder.startswith(rel + "/"):
                raise ValueError("can't move a folder into itself")
            dst = self.paths.rel(unique_path(dest_dir / src.name))
            pairs.append((rel, dst))
            out.append(dst)
        self._apply(pairs)
        return out

    def trash(self, rels: list[str]) -> None:
        stamp = f"{ArchivePaths.DATA}/trash/{int(time.time() * 1000)}"
        self._apply([(rel, f"{stamp}/{rel}") for rel in rels])

    def import_files(self, sources: list[Path], dest_folder: str) -> list[str]:
        dest_dir = self.paths.abs(dest_folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for src in sources:
            dst = unique_path(dest_dir / Path(src).name)
            shutil.copy2(src, dst)
            out.append(self.paths.rel(dst))
        self.scan()
        return out

    def undo(self) -> bool:
        """replays the last op backwards. never overwrites: if something now sits at an original
        path the file comes back as "name (2)". returns False (entry kept) if a file is gone."""
        with self.lock:
            if not self._undo:
                return False
            pairs = self._undo[-1]
            back, taken = [], set()
            for src, dst in reversed(pairs):
                if not self.paths.abs(dst).exists():
                    return False
                target = self.paths.abs(src)
                if target.exists() or src in taken or self.store.get(src) is not None:
                    target = unique_path(target)
                    while self.paths.rel(target) in taken or self.store.get(self.paths.rel(target)):
                        target = unique_path(target.with_name(target.stem + " (2)" + target.suffix))
                rel = self.paths.rel(target)
                taken.add(rel)
                back.append((dst, rel))
            try:
                self._apply_locked(back, record=False)
            except (OSError, sqlite3.Error):
                return False
            self._undo.pop()
            return True
