"""tiktok video extraction, modeled on how yt-dlp does it today.

the video page embeds its data as json in <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">;
the item lives at __DEFAULT_SCOPE__ -> webapp.video-detail -> itemInfo.itemStruct.
video.bitrateInfo lists every encode (h264 and h265/bytevc1 at several sizes); downloadAddr
is the watermarked one and never used. the cdn 403s without a tiktok referer plus the
cookies the page set, so items carry their own http headers and share the page's session.

sometimes tiktok serves a bot-check page instead: a base64 json challenge where you find the
number whose sha256 (appended to a given prefix) matches, then send the answer as a cookie.
"""

import base64
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

from .extractor import ExtractionError
from .models import Media, VideoItem, VideoVariant

WEB = "https://www.tiktok.com"
HEADERS = {"Referer": WEB + "/"}
REHYDRATION_RE = re.compile(r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)

_VIDEO_PATHS = [
    re.compile(r"^/@[^/]+/video/(\d+)"),
    re.compile(r"^/embed(?:/v2)?/(\d+)"),
    re.compile(r"^/share/video/(\d+)"),
    re.compile(r"^/v/(\d+)(?:\.html)?"),
]
_PHOTO_PATH = re.compile(r"^/@[^/]+/photo/(\d+)")
_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")


class UnsupportedMedia(ExtractionError):
    pass


def _host(url: str) -> str:
    if "://" not in url:
        url = "https://" + url.strip()
    return (urlparse(url).hostname or "").lower()


def is_tiktok_url(url: str) -> bool:
    h = _host(url)
    return h == "tiktok.com" or h.endswith(".tiktok.com")


def parse_tiktok_url(url: str) -> tuple[str | None, bool]:
    """(video id, needs_resolve). short links have no id until their redirect is followed."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _SHORT_HOSTS or parsed.path.startswith("/t/"):
        return None, True
    if _PHOTO_PATH.match(parsed.path):
        raise UnsupportedMedia("TikTok photo posts aren't supported – only videos")
    for rx in _VIDEO_PATHS:
        if m := rx.match(parsed.path):
            return m.group(1), False
    raise ExtractionError(f"couldn't find a TikTok video id in: {url}")


# ---------- bot check ----------

def _attr_by_id(html: str, el_id: str, attr: str = "class") -> str | None:
    tag = re.search(rf'<[^>]*\bid="{re.escape(el_id)}"[^>]*>', html)
    if not tag:
        return None
    m = re.search(rf'\b{attr}="([^"]*)"', tag.group(0))
    return m.group(1) if m else None


def is_challenge(html: str) -> bool:
    return not REHYDRATION_RE.search(html) and _attr_by_id(html, "cs") is not None


def solve_waf(html: str) -> dict[str, str]:
    """port of yt-dlp's _solve_challenge_and_set_cookies. returns {cookie name: value}."""
    raw = _attr_by_id(html, "cs")
    wci_name = _attr_by_id(html, "wci")
    if not raw or not wci_name:
        raise ExtractionError("tiktok sent an unexpected page (no video data, no bot check)")
    try:
        challenge = json.loads(base64.b64decode(raw + "==="))
        expected = base64.b64decode(challenge["v"]["c"])
        base = hashlib.sha256(base64.b64decode(challenge["v"]["a"]))
    except (ValueError, KeyError, TypeError) as e:
        raise ExtractionError(f"couldn't read tiktok's bot check: {e}") from e
    for i in range(1_000_001):
        number = str(i).encode()
        h = base.copy()
        h.update(number)
        if h.digest() == expected:
            challenge["d"] = base64.b64encode(number).decode()
            break
    else:
        raise ExtractionError("couldn't solve tiktok's bot check")
    cookies = {wci_name: base64.b64encode(json.dumps(challenge, separators=(",", ":")).encode()).decode()}
    rci_name, rci_value = _attr_by_id(html, "rci"), _attr_by_id(html, "rs")
    if rci_name and rci_value:
        cookies[rci_name] = rci_value
    return cookies


# ---------- parsing ----------

def _codec(entry: dict) -> str | None:
    text = f"{entry.get('CodecType') or ''} {(entry.get('PlayAddr') or {}).get('UrlKey') or ''}".lower()
    if "bytevc2" in text:
        return None  # nothing plays it
    if "265" in text or "hvc" in text or "hev" in text or "bytevc1" in text:
        return "h265"
    return "h264"


def can_convert() -> bool:
    from .downloader import ffmpeg_path  # h265 picks rely on ffmpeg turning them into h264
    return bool(ffmpeg_path())


def pick_variant(video: dict) -> tuple[VideoVariant, str]:
    """best encode: most pixels, then h264 over h265 (no re-encode needed), then bitrate.
    without ffmpeg an h265 file would stay h265 (no phone playback), so h264 comes first."""
    cands = []
    for b in video.get("bitrateInfo") or []:
        pa = b.get("PlayAddr") or {}
        urls = [u for u in pa.get("UrlList") or [] if isinstance(u, str) and u.startswith("http")]
        codec = _codec(b)
        if not urls or codec is None:
            continue
        w, h = int(pa.get("Width") or 0), int(pa.get("Height") or 0)
        cands.append(((w * h, codec == "h264", int(b.get("Bitrate") or 0)), VideoVariant(urls[0], w or None, h or None), codec))
    play = video.get("playAddr")
    if isinstance(play, str) and play.startswith("http"):
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        cands.append(((w * h, True, int(video.get("bitrate") or 0) - 1), VideoVariant(play, w or None, h or None), "h264"))
    if not cands:
        raise ExtractionError("tiktok returned the video but no playable stream")
    if can_convert():
        _, variant, codec = max(cands, key=lambda c: c[0])
    else:
        _, variant, codec = max(cands, key=lambda c: (c[0][1], c[0][0], c[0][2]))
    return variant, codec


def parse_page(html: str, video_id: str) -> Media:
    m = REHYDRATION_RE.search(html)
    if not m:
        raise ExtractionError("no video data in the tiktok page (layout changed or blocked)")
    try:
        detail = json.loads(m.group(1))["__DEFAULT_SCOPE__"]["webapp.video-detail"]
    except (ValueError, KeyError, TypeError) as e:
        raise ExtractionError(f"couldn't read tiktok's video data: {e}") from e
    if detail.get("statusCode"):
        raise ExtractionError(f"tiktok says: {detail.get('statusMsg') or detail.get('statusCode')}"
                              " (private, removed, or region-locked?)")
    item = (detail.get("itemInfo") or {}).get("itemStruct") or {}
    if item.get("imagePost"):
        raise UnsupportedMedia("TikTok photo posts aren't supported – only videos")
    video = item.get("video") or {}
    variant, _ = pick_variant(video)
    return Media(
        shortcode=str(item.get("id") or video_id),
        username=(item.get("author") or {}).get("uniqueId"),
        caption=item.get("desc") or None,
        videos=[VideoItem(variants=[variant], duration=video.get("duration"), thumbnail=video.get("cover"),
                          has_audio=True, http_headers=dict(HEADERS))],
        source="tiktok_web",
    )


# ---------- network ----------

def _resolve_short(url: str, client) -> str:
    for _ in range(3):
        r = client.get(url, allow_redirects=False)
        loc = r.headers.get("location") or r.headers.get("Location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            break
        url = urljoin(url, loc)
        if not is_tiktok_url(url):  # never follow a short link off tiktok
            break
        try:
            vid, again = parse_tiktok_url(url)
        except UnsupportedMedia:
            raise
        except ExtractionError:
            break
        if vid:
            return vid
        if not again:
            break
    raise ExtractionError("that TikTok link doesn't point to a video")


def extract(url: str, client) -> Media:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    vid, needs_resolve = parse_tiktok_url(url)
    try:
        if needs_resolve:
            vid = _resolve_short(url, client)
        page_url = f"{WEB}/@_/video/{vid}"  # tiktok redirects to the real username
        for attempt in range(2):
            r = client.get(page_url, headers=HEADERS)
            if r.status_code != 200:
                raise ExtractionError(f"tiktok returned http {r.status_code}")
            if not is_challenge(r.text):
                return parse_page(r.text, vid)
            if attempt:
                break
            for name, value in solve_waf(r.text).items():
                client.s.cookies.set(name, value, domain=".tiktok.com")
    except ExtractionError:
        raise
    except Exception as e:  # noqa: BLE001 - network errors etc.
        raise ExtractionError(f"couldn't reach tiktok: {type(e).__name__}: {e}") from e
    raise ExtractionError("tiktok blocked the request (bot check) – try again in a minute")
