"""ffmpeg helpers for the archive: probe, thumbnails and shrinking clips under discord's limit."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..downloader import ffmpeg_path, ffprobe_path

# keep ffmpeg from flashing a console window when the app runs under pythonw
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def available() -> bool:
    return bool(ffmpeg_path() and ffprobe_path())


def _run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=_NO_WINDOW, **kw)


@dataclass
class ProbeInfo:
    duration: float
    width: int
    height: int
    vcodec: str | None
    acodec: str | None


def probe(path: Path) -> ProbeInfo:
    out = _run([ffprobe_path(), "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", path]).stdout
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return ProbeInfo(
        duration=float(data.get("format", {}).get("duration") or 0),
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        vcodec=v.get("codec_name"),
        acodec=a.get("codec_name"),
    )


def thumbnail(src: Path, dest: Path, width: int = 320) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.jpg")
    base = [ffmpeg_path(), "-y", "-loglevel", "error"]
    tail = ["-i", src, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", tmp]
    try:
        _run(base + ["-ss", "1"] + tail)
    except subprocess.CalledProcessError:
        pass
    if not tmp.exists() or tmp.stat().st_size == 0:  # clip shorter than 1s: seek lands past the end
        _run(base + tail)
    os.replace(tmp, dest)


@dataclass
class ShrinkResult:
    path: Path
    size: int
    low_quality: bool


def _even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def shrink(src: Path, dest: Path, limit_bytes: int) -> ShrinkResult:
    info = probe(src)
    duration = max(info.duration, 0.5)
    total_kbps = limit_bytes * 0.95 * 8 / duration / 1000
    has_audio = info.acodec is not None
    audio_kbps = (128 if total_kbps >= 640 else max(24, total_kbps * 0.2)) if has_audio else 0
    video_kbps = max(total_kbps - audio_kbps, 20)
    low_quality = video_kbps < 300

    vf = []
    short = min(info.width, info.height)
    if video_kbps < 1500 and short > 720:
        f = 720 / short
        vf = ["-vf", f"scale={_even(info.width * f)}:{_even(info.height * f)}"]

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.mp4")
    size = 0
    try:
        for _ in range(3):  # first try + 2 retries at 85% bitrate
            v = int(video_kbps)
            cmd = [ffmpeg_path(), "-y", "-loglevel", "error", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", *vf,
                   "-c:v", "libx264", "-preset", "medium", "-b:v", f"{v}k", "-maxrate", f"{v}k",
                   "-bufsize", f"{2 * v}k", "-pix_fmt", "yuv420p"]
            cmd += ["-c:a", "aac", "-b:a", f"{int(audio_kbps)}k"] if has_audio else ["-an"]
            _run(cmd + ["-movflags", "+faststart", tmp])
            size = tmp.stat().st_size
            if size <= limit_bytes:
                break
            video_kbps *= 0.85
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return ShrinkResult(dest, size, low_quality)
