"""where the archive lives. everything is relative to one root folder (the same downloads/
folder the docker web ui writes to), app data goes in <root>/.reelgrab/."""

import os
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "downloads"


def default_root(settings_root: str | None = None) -> Path:
    return Path(os.environ.get("REELGRAB_ARCHIVE_DIR") or settings_root or DEFAULT_ROOT)


class ArchivePaths:
    INBOX = "_inbox"
    DATA = ".reelgrab"

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.inbox = self.root / self.INBOX
        self.data = self.root / self.DATA
        self.db = self.data / "archive.db"
        self.thumbs = self.data / "thumbs"
        self.send = self.data / "send"
        self.trash = self.data / "trash"

    def ensure(self) -> None:
        for d in (self.root, self.inbox, self.data, self.thumbs, self.send, self.trash):
            d.mkdir(parents=True, exist_ok=True)

    def rel(self, p: Path | str) -> str:
        return Path(p).resolve().relative_to(self.root).as_posix()

    def abs(self, rel: str) -> Path:
        return self.root.joinpath(*rel.split("/")) if rel else self.root
