"""turns an instagram reel/post url into direct cdn video urls.

strategies, tried in order until one gives us a video:
  0. v1 media info api      (only if you gave it a sessionid / cookies file)
  1. polaris graphql         POST /api/graphql  doc_id=27130156389949648  (same as yt-dlp)
  2. shortcode web_info gql  POST /graphql/query doc_id=27128499623469141 (same as instaloader)
  3. post page scrape        relay prefetch json embedded in /p/<code>/
  4. embed page scrape       /p/<code>/embed/captioned/

instagram rotates doc_ids every few months. if 1 and 2 start failing, grab fresh ones from
the network tab (filter "graphql") or from yt-dlp / instaloader source, and override via env:
  REELGRAB_POLARIS_DOC_ID, REELGRAB_WEBINFO_DOC_ID
"""

import html as htmllib
import json
import os
import re
from typing import Any, Iterator
from urllib.parse import urlparse

from .client import BASE, IGClient
from .models import Media, VideoItem, VideoVariant
from .shortcode import parse_url, shortcode_to_pk

POLARIS_DOC_ID = os.environ.get("REELGRAB_POLARIS_DOC_ID", "27130156389949648")
WEBINFO_DOC_ID = os.environ.get("REELGRAB_WEBINFO_DOC_ID", "27128499623469141")

_JSON_SCRIPT_RE = re.compile(r"<script\b[^>]*type=\"application/json\"[^>]*>(.*?)</script>", re.S)
_VIDEO_URL_RE = re.compile(r'"video_url"\s*:\s*"(https?://[^"]+)"')


class ExtractionError(Exception):
    pass


class LoginRequired(ExtractionError):
    pass


# ---------- json helpers ----------

def _loads(text: str) -> Any:
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[9:]
    return json.loads(text)


def _walk(obj: Any, depth: int = 0) -> Iterator[dict]:
    """yield every dict in a nested blob. also dives into json-encoded strings (embed page does that)."""
    if depth > 60:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)
    elif isinstance(obj, str) and len(obj) > 50 and obj[:1] in "{[" and "video" in obj:
        try:
            yield from _walk(json.loads(obj), depth + 1)
        except ValueError:
            pass


def _has_video(node: dict) -> bool:
    return bool(node.get("video_versions")) or isinstance(node.get("video_url"), str)


def _find_media_node(blob: Any, shortcode: str) -> dict | None:
    """find the post object: prefer one whose code matches, fall back to any node with video."""
    fallback = None
    for d in _walk(blob):
        code = d.get("code") or d.get("shortcode")
        is_post = code == shortcode or code == shortcode[:11]
        if is_post and (_has_video(d) or d.get("carousel_media") or d.get("edge_sidecar_to_children")):
            return d
        if fallback is None and _has_video(d):
            fallback = d
    return fallback


# ---------- normalizing v1 / legacy shapes into our model ----------

def _item_from_node(node: dict) -> VideoItem | None:
    variants = [
        VideoVariant(url=v["url"], width=v.get("width"), height=v.get("height"))
        for v in node.get("video_versions") or []
        if isinstance(v, dict) and v.get("url")
    ]
    if not variants and isinstance(node.get("video_url"), str):
        dims = node.get("dimensions") or {}
        variants.append(VideoVariant(node["video_url"], dims.get("width"), dims.get("height")))
    if not variants:
        return None

    thumb = None
    cands = (node.get("image_versions2") or {}).get("candidates") or []
    if cands:
        thumb = cands[0].get("url")
    thumb = thumb or node.get("display_url") or node.get("thumbnail_src")

    return VideoItem(
        variants=variants,
        dash_manifest=node.get("video_dash_manifest") or None,
        duration=node.get("video_duration"),
        thumbnail=thumb,
        has_audio=node.get("has_audio"),
    )


def normalize(node: dict, shortcode: str, source: str) -> Media:
    children = node.get("carousel_media") or [
        e.get("node", {}) for e in (node.get("edge_sidecar_to_children") or {}).get("edges", [])
    ]
    items = [_item_from_node(c) for c in children] if children else [_item_from_node(node)]
    items = [i for i in items if i]

    user = node.get("user") or node.get("owner") or {}
    caption = (node.get("caption") or {}).get("text") if isinstance(node.get("caption"), dict) else None
    if caption is None:
        edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        caption = edges[0]["node"].get("text") if edges else None

    return Media(
        shortcode=shortcode,
        username=user.get("username"),
        caption=caption,
        videos=items,
        source=source,
    )


# ---------- strategies ----------

def _strat_v1_api(c: IGClient, sc: str, pk: int, url: str) -> dict | None:
    r = c.get(f"{BASE}/api/v1/media/{pk}/info/", headers=c.api_headers(url))
    if r.status_code != 200:
        raise ExtractionError(f"v1 api http {r.status_code}")
    items = _loads(r.text).get("items") or []
    return items[0] if items else None


def _strat_polaris(c: IGClient, sc: str, pk: int, url: str) -> dict | None:
    if not c.lsd:
        raise ExtractionError("no LSD token from homepage")
    name = "PolarisLoggedOutDesktopWWWPostRootContentQuery"
    headers = c.api_headers(url) | {
        "X-FB-Friendly-Name": name,
        "X-FB-LSD": c.lsd,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "lsd": c.lsd,
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": name,
        "server_timestamps": "true",
        "variables": json.dumps({"media_id": str(pk)}, separators=(",", ":")),
        "doc_id": POLARIS_DOC_ID,
    }
    r = c.post(f"{BASE}/api/graphql", headers=headers, data=data)
    if r.status_code != 200:
        raise ExtractionError(f"polaris graphql http {r.status_code}")
    media = ((_loads(r.text).get("data") or {}).get("xig_polaris_media") or {})
    return media.get("if_not_gated_logged_out")


def _strat_webinfo(c: IGClient, sc: str, pk: int, url: str) -> dict | None:
    headers = c.api_headers(url) | {"Content-Type": "application/x-www-form-urlencoded"}
    variables = {
        "shortcode": sc,
        "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
    }
    data = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "doc_id": WEBINFO_DOC_ID,
        "server_timestamps": "true",
    }
    if c.lsd:
        data["lsd"] = c.lsd
    r = c.post(f"{BASE}/graphql/query", headers=headers, data=data)
    if r.status_code != 200:
        raise ExtractionError(f"web_info graphql http {r.status_code}")
    info = (_loads(r.text).get("data") or {}).get("xdt_api__v1__media__shortcode__web_info") or {}
    items = info.get("items") or []
    return items[0] if items else None


def _scrape_html(html: str, sc: str) -> dict | None:
    blobs = []
    for raw in _JSON_SCRIPT_RE.findall(html):
        try:
            blobs.append(json.loads(raw))
        except ValueError:
            continue
    node = _find_media_node(blobs, sc)
    if node:
        return node

    # last resort: regex the (possibly multi-escaped) video_url out of inline js
    text = html
    for _ in range(3):
        if m := _VIDEO_URL_RE.search(text):
            return {"shortcode": sc, "video_url": json.loads(f'"{m.group(1)}"')}
        text = text.replace('\\"', '"').replace("\\/", "/")
    return None


def _strat_page(c: IGClient, sc: str, pk: int, url: str) -> dict | None:
    r = c.get(f"{BASE}/p/{sc}/", headers={"Accept": "text/html", "Referer": BASE + "/"})
    if urlparse(str(r.url)).path.startswith("/accounts/login"):
        raise LoginRequired("post page redirected to login (anon rate limit or private post)")
    return _scrape_html(r.text, sc)


def _strat_embed(c: IGClient, sc: str, pk: int, url: str) -> dict | None:
    r = c.get(f"{BASE}/p/{sc}/embed/captioned/", headers={"Accept": "text/html", "Referer": BASE + "/"})
    if r.status_code != 200:
        raise ExtractionError(f"embed http {r.status_code}")
    return _scrape_html(htmllib.unescape(r.text) if "&quot;video_url" in r.text else r.text, sc)


STRATEGIES = [
    ("polaris_graphql", _strat_polaris),
    ("webinfo_graphql", _strat_webinfo),
    ("post_page", _strat_page),
    ("embed_page", _strat_embed),
]


def _ruling(c: IGClient, pk: int, url: str) -> str | None:
    """instagram's own 'can a logged-out user see this' check. gives a readable reason when it's gated."""
    try:
        r = c.get(
            f"{BASE}/api/v1/web/get_ruling_for_content/",
            params={"content_type": "MEDIA", "target_id": str(pk)},
            headers=c.api_headers(url),
        )
        j = _loads(r.text)
    except Exception:
        return None
    if j.get("status") == "ok":
        return None
    return ": ".join(x for x in (j.get("title"), j.get("description")) if x) or None


def extract(url: str, client: IGClient | None = None, verbose: bool = False) -> Media:
    c = client or IGClient()
    code, is_share = parse_url(url)
    try:
        if is_share:
            code, _ = parse_url(c.resolve_share(code))
        c.bootstrap()
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"couldn't reach instagram: {type(e).__name__}: {e}") from e
    sc = code
    pk = shortcode_to_pk(sc)
    canonical = f"{BASE}/reel/{sc}/"

    strategies = ([("v1_api", _strat_v1_api)] if c.logged_in else []) + STRATEGIES
    ruling = None if c.logged_in else _ruling(c, pk, canonical)

    errors = []
    for name, fn in strategies:
        try:
            node = fn(c, sc, pk, canonical)
            if node:
                media = normalize(node, sc, name)
                if media.videos:
                    return media
                errors.append(f"{name}: post found but it has no video")
            else:
                errors.append(f"{name}: empty response")
        except LoginRequired as e:
            errors.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001 - we want every strategy to get a shot
            errors.append(f"{name}: {type(e).__name__}: {e}")
        if verbose:
            print(f"[reelgrab] {errors[-1]}")
        c.polite_sleep()

    hint = ""
    if ruling:
        hint = f"\ninstagram says: {ruling}"
    if not c.logged_in:
        hint += "\nif the reel is public, you're probably being rate-limited as a logged-out client. " \
                "set IG_SESSIONID (or IG_COOKIES_FILE) and retry."
    raise ExtractionError("all strategies failed:\n  " + "\n  ".join(errors) + hint)
