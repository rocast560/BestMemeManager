"""user settings, kept outside the archive root (the root itself is one of the settings)."""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def settings_file() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "reelgrab" / "settings.json"


@dataclass
class Settings:
    hotkey: str = "Ctrl+Shift+M"
    limit_mb: float = 10.0
    auto_paste: bool = True
    archive_dir: str | None = None

    @property
    def limit_bytes(self) -> int:
        return int(self.limit_mb * 1024 * 1024)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
