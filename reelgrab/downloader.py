import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .client import BASE, IGClient
from .dash import best_pair, parse_mpd
from .models import Media, VideoItem

ProgressFn = Callable[[int, int | None], None]
CHUNK = 1 << 16
# no console window per ffmpeg call when running under pythonw (the archive app)
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


@dataclass
class Plan:
    mode: str  # "progressive" | "dash"
    width: int | None
    height: int | None
    video_url: str
    audio_url: str | None = None


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "reel"


def ffmpeg_path() -> str | None:
    return shutil.which(os.environ.get("FFMPEG", "ffmpeg"))


def ffprobe_path() -> str | None:
    ff = ffmpeg_path()
    if ff:
        sibling = shutil.which(os.path.join(os.path.dirname(ff), "ffprobe"))
        if sibling:
            return sibling
    return shutil.which("ffprobe")


def probe_codecs(path: str) -> dict[str, str]:
    """{"video": "h264", "audio": "aac"} for the first stream of each kind."""
    out = subprocess.run(
        [ffprobe_path(), "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", path],
        capture_output=True, text=True, check=True, **_NO_WINDOW,
    ).stdout
    codecs: dict[str, str] = {}
    for s in json.loads(out or "{}").get("streams", []):
        if s.get("codec_type") and s.get("codec_name"):
            codecs.setdefault(s["codec_type"], s["codec_name"])
    return codecs


def plan_for(item: VideoItem, quality: str = "best") -> Plan:
    prog = item.best_variant()
    prog_plan = Plan("progressive", prog.width, prog.height, prog.url) if prog else None

    if quality == "progressive" or not item.dash_manifest or not ffmpeg_path():
        if not prog_plan:
            raise RuntimeError("no progressive mp4 and dash is unavailable (need ffmpeg + manifest)")
        return prog_plan

    try:
        v, a = best_pair(parse_mpd(item.dash_manifest))
    except Exception:
        v = a = None

    # only bother muxing when dash actually beats the progressive file
    if v and (not prog_plan or v.width * v.height > (prog_plan.width or 0) * (prog_plan.height or 0)):
        return Plan("dash", v.width, v.height, v.url, a.url if a else None)
    if prog_plan:
        return prog_plan
    if v:
        return Plan("dash", v.width, v.height, v.url, a.url if a else None)
    raise RuntimeError("couldn't find any downloadable video track")


def fetch(client: IGClient, url: str, dest: str, progress: ProgressFn | None = None) -> None:
    headers = {"Referer": BASE + "/", "Origin": BASE, "Accept": "*/*"}
    r = client.get(url, headers=headers, stream=True, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"cdn returned http {r.status_code} (url may have expired, re-extract)")
    total = int(r.headers.get("content-length") or 0) or None
    done = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(CHUNK):
            if not chunk:
                continue
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    r.close()


def mux(video: str, audio: str | None, out: str) -> None:
    cmd = [ffmpeg_path(), "-y", "-loglevel", "error", "-i", video]
    if audio:
        cmd += ["-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c", "copy", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, **_NO_WINDOW)


def replace_retry(src: str, dst: str, tries: int = 10) -> None:
    """os.replace, retried briefly: on windows a video player previewing dst holds it open."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.3)


def make_compatible(path: str) -> bool:
    """re-encode to h264/aac in place unless it already is. instagram's dash video is often
    vp9, which desktop browsers play but ios (discord mobile, imessage...) won't. returns
    True if the file was re-encoded."""
    if not ffmpeg_path() or not ffprobe_path():
        return False
    codecs = probe_codecs(path)
    v_ok = codecs.get("video") in (None, "h264")
    a_ok = codecs.get("audio") in (None, "aac")
    if v_ok and a_ok:
        return False
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp.mp4"  # unique so concurrent runs can't share it
    cmd = [ffmpeg_path(), "-y", "-loglevel", "error", "-i", path, "-map", "0:v:0?", "-map", "0:a:0?"]
    cmd += ["-c:v", "copy"] if v_ok else [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ]
    cmd += ["-c:a", "copy"] if a_ok else ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-movflags", "+faststart", tmp]
    try:
        subprocess.run(cmd, check=True, **_NO_WINDOW)
        replace_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return True


def download(
    media: Media,
    client: IGClient,
    out_dir: str = ".",
    quality: str = "best",
    progress: ProgressFn | None = None,
) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    stem = _safe(f"{media.username}_{media.shortcode}" if media.username else media.shortcode)
    paths = []

    for i, item in enumerate(media.videos, start=1):
        suffix = f"_{i}" if len(media.videos) > 1 else ""
        out = os.path.join(out_dir, f"{stem}{suffix}.mp4")
        part = os.path.join(out_dir, f"{stem}{suffix}.part.mp4")  # watchers skip .part until it's finished
        plan = plan_for(item, quality)

        if plan.mode == "progressive":
            fetch(client, plan.video_url, part, progress)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                v = os.path.join(tmp, "v.mp4")
                fetch(client, plan.video_url, v, progress)
                a = None
                if plan.audio_url:
                    a = os.path.join(tmp, "a.mp4")
                    fetch(client, plan.audio_url, a, progress)
                mux(v, a, part)
        try:
            make_compatible(part)
            os.replace(part, out)
        finally:
            if os.path.exists(part):
                os.remove(part)
        paths.append(out)
    return paths
