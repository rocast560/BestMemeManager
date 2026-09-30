"""sqlite side of the archive: tags, favorites, send history and cached probe info.

files on disk are the source of truth. sync() is fed the current file listing and brings the
db in line, treating "one file vanished + a new file with the same name and size appeared" as a move,
and an unambiguous same size+mtime pair as a rename, so tags survive explorer moves/renames.
"""

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime REAL, duration REAL,
  width INTEGER, height INTEGER, vcodec TEXT, favorite INTEGER DEFAULT 0,
  send_count INTEGER DEFAULT 0, last_sent REAL);
CREATE TABLE IF NOT EXISTS tags(
  rel_path TEXT REFERENCES clips(rel_path) ON UPDATE CASCADE ON DELETE CASCADE,
  tag TEXT, PRIMARY KEY(rel_path, tag));
"""
HIDDEN = ".reelgrab/"  # trashed clips keep their row here so undo restores tags
_COLS = "rel_path, size, mtime, duration, width, height, vcodec, favorite, send_count, last_sent"


@dataclass
class Clip:
    rel_path: str
    size: int
    mtime: float
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    favorite: bool = False
    send_count: int = 0
    last_sent: float | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return self.rel_path.rpartition("/")[0]

    @property
    def name(self) -> str:
        return self.rel_path.rpartition("/")[2]


def _basename(rel: str) -> str:
    return rel.rpartition("/")[2]


class Store:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._open()
        except sqlite3.DatabaseError:
            self.path.rename(self.path.with_name(f"{self.path.name}.bad-{int(time.time())}"))
            self._open()

    def _open(self) -> None:
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        try:
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.executescript(SCHEMA)
        except sqlite3.DatabaseError:
            self.db.close()
            raise

    # ---------- reads ----------

    def _clips(self, where: str = "", args: tuple = (), order: str = "rel_path") -> list[Clip]:
        with self._lock:
            rows = self.db.execute(
                f"SELECT {_COLS} FROM clips WHERE rel_path NOT LIKE ? {where} ORDER BY {order}",
                (HIDDEN + "%", *args)).fetchall()
            tags: dict[str, list[str]] = {}
            for rel, tag in self.db.execute("SELECT rel_path, tag FROM tags ORDER BY tag"):
                tags.setdefault(rel, []).append(tag)
        return [Clip(*r[:7], bool(r[7]), r[8], r[9], tags.get(r[0], [])) for r in rows]

    def get(self, rel: str) -> Clip | None:
        found = self._clips("AND rel_path = ?", (rel,))
        return found[0] if found else None

    def all(self) -> list[Clip]:
        return self._clips()

    def favorites(self) -> list[Clip]:
        return self._clips("AND favorite = 1", order="send_count DESC, rel_path")

    def recent(self, limit: int = 50) -> list[Clip]:
        return self._clips("AND last_sent IS NOT NULL", order="last_sent DESC")[:limit]

    def search(self, query: str) -> list[Clip]:
        tokens = query.lower().split()
        clips = self._clips(order="send_count DESC, COALESCE(last_sent, 0) DESC, rel_path")
        if not tokens:
            return clips
        return [c for c in clips if all(t in (c.rel_path + " " + " ".join(c.tags)).lower() for t in tokens)]

    # ---------- writes ----------

    def sync(self, files: dict[str, tuple[int, float]]) -> tuple[list[str], list[str]]:
        """returns (new or changed rels, removed rels)."""
        with self._lock, self.db:
            known = {r: (s, m) for r, s, m in self.db.execute(
                "SELECT rel_path, size, mtime FROM clips WHERE rel_path NOT LIKE ?", (HIDDEN + "%",))}
            new = [r for r in files if r not in known]
            gone = [r for r in known if r not in files]
            removed = []
            for old in gone:
                size, mtime = known[old]
                # moved (same name + size), else renamed (explorer keeps size + mtime; must be unambiguous)
                match = next((n for n in new if _basename(n) == _basename(old) and files[n][0] == size), None)
                if match is None:
                    same = [n for n in new if tuple(files[n]) == (size, mtime)]
                    twins = [g for g in gone if tuple(known[g]) == (size, mtime)]
                    match = same[0] if len(same) == 1 and len(twins) == 1 else None
                if match:
                    new.remove(match)
                    self.db.execute("UPDATE clips SET rel_path = ?, mtime = ? WHERE rel_path = ?",
                                    (match, files[match][1], old))
                else:
                    self.db.execute("DELETE FROM clips WHERE rel_path = ?", (old,))
                    removed.append(old)
            for rel in new:
                self.db.execute("INSERT INTO clips(rel_path, size, mtime) VALUES (?, ?, ?)", (rel, *files[rel]))
            changed = [r for r in files if r in known and tuple(files[r]) != tuple(known[r])]
            for rel in changed:
                self.db.execute("UPDATE clips SET size = ?, mtime = ?, duration = NULL, width = NULL, "
                                "height = NULL, vcodec = NULL WHERE rel_path = ?", (*files[rel], rel))
        return sorted(new + changed), sorted(removed)

    def rename_prefix(self, old: str, new: str) -> None:
        with self._lock, self.db:
            rows = self.db.execute(r"SELECT rel_path FROM clips WHERE rel_path = ? OR rel_path LIKE ? ESCAPE '\'",
                                   (old, old.replace("%", r"\%").replace("_", r"\_") + "/%")).fetchall()
            for (rel,) in rows:
                if rel == old or rel.startswith(old + "/"):
                    self.db.execute("UPDATE clips SET rel_path = ? WHERE rel_path = ?", (new + rel[len(old):], rel))

    def remove(self, rel: str) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM clips WHERE rel_path = ?", (rel,))

    def set_probe(self, rel: str, duration, width, height, vcodec) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE clips SET duration = ?, width = ?, height = ?, vcodec = ? WHERE rel_path = ?",
                            (duration, width, height, vcodec, rel))

    def set_tags(self, rel: str, tags: list[str]) -> None:
        clean = sorted({t.strip().lower() for t in tags if t.strip()})
        with self._lock, self.db:
            self.db.execute("DELETE FROM tags WHERE rel_path = ?", (rel,))
            self.db.executemany("INSERT INTO tags(rel_path, tag) VALUES (?, ?)", [(rel, t) for t in clean])

    def set_favorite(self, rel: str, fav: bool) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE clips SET favorite = ? WHERE rel_path = ?", (int(fav), rel))

    def log_send(self, rel: str, ts: float | None = None) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE clips SET send_count = send_count + 1, last_sent = ? WHERE rel_path = ?",
                            (time.time() if ts is None else ts, rel))
