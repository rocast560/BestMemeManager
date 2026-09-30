import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.background import BackgroundTask

from .client import BASE, IGClient
from .downloader import download, ffmpeg_path, plan_for
from .extractor import ExtractionError, extract
from .shortcode import InvalidReelUrl

SAVE_DIR = os.environ.get("REELGRAB_SAVE_DIR")  # set in docker so files also land in the mounted volume
STATIC = Path(__file__).parent / "static"
IG_CDN_HOSTS = (".cdninstagram.com", ".fbcdn.net")
TT_CDN_HOSTS = (".tiktokcdn.com", ".tiktokcdn-us.com", ".tiktokcdn-eu.com", ".ibyteimg.com")
CDN_HOSTS = IG_CDN_HOSTS + TT_CDN_HOSTS

app = FastAPI(title="reelgrab", docs_url="/api/docs")

# one shared session keeps cookies/csrf warm between requests; curl sessions aren't thread safe
_client = IGClient()
_lock = threading.Lock()


def _extract(url: str):
    try:
        return extract(url, _client)
    except InvalidReelUrl as e:
        raise HTTPException(400, str(e))
    except ExtractionError as e:
        raise HTTPException(502, str(e))


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz():
    return {"ok": True, "ffmpeg": bool(ffmpeg_path()), "logged_in": _client.logged_in}


@app.get("/api/info")
def info(url: str = Query(...), quality: str = "best"):
    with _lock:
        media = _extract(url)
    d = media.to_dict()
    d["plans"] = [plan_for(v, quality).__dict__ | {"video_url": None, "audio_url": None} for v in media.videos]
    return d


@app.get("/api/download")
def dl(url: str = Query(...), quality: str = "best"):
    tmp = None
    out_dir = SAVE_DIR
    if not out_dir:
        out_dir = tmp = tempfile.mkdtemp(prefix="reelgrab-")

    with _lock:
        media = _extract(url)
        try:
            paths = download(media, _client, out_dir, quality)
        except Exception as e:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(502, f"download failed: {e}")

    cleanup = BackgroundTask(shutil.rmtree, tmp, ignore_errors=True) if tmp else None
    if len(paths) == 1:
        return FileResponse(paths[0], media_type="video/mp4", filename=os.path.basename(paths[0]),
                            background=cleanup)

    zdir = tmp or tempfile.mkdtemp(prefix="reelgrab-")
    zpath = os.path.join(zdir, f"{media.shortcode}.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        for p in paths:
            z.write(p, os.path.basename(p))
    return FileResponse(zpath, media_type="application/zip", filename=os.path.basename(zpath),
                        background=BackgroundTask(shutil.rmtree, zdir, ignore_errors=True))


@app.get("/api/thumb")
def thumb(u: str = Query(...)):
    # ig/tiktok cdns block cross-origin image embeds, so proxy thumbnails (cdn hosts only, no open proxy)
    host = urlparse(u).hostname or ""
    if urlparse(u).scheme != "https" or not host.endswith(CDN_HOSTS):
        raise HTTPException(400, "not an instagram/tiktok cdn url")
    with _lock:
        referer = "https://www.tiktok.com/" if host.endswith(TT_CDN_HOSTS) else BASE + "/"
        r = _client.get(u, headers={"Referer": referer})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "thumbnail fetch failed")
    return Response(r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "max-age=3600"})
