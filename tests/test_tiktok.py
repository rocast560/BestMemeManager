import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from reelgrab import tiktok
from reelgrab.extractor import ExtractionError

FIXTURE = (Path(__file__).parent / "fixtures" / "tiktok_video.html").read_text(encoding="utf-8")
VID = "6718335390845095173"
TT_REFERER = {"Referer": "https://www.tiktok.com/"}


def _page(mutate=None) -> str:
    """fixture page, optionally with its item edited."""
    m = tiktok.REHYDRATION_RE.search(FIXTURE)
    data = json.loads(m.group(1))
    if mutate:
        mutate(data["__DEFAULT_SCOPE__"]["webapp.video-detail"])
    return FIXTURE[:m.start(1)] + json.dumps(data) + FIXTURE[m.end(1):]


class Resp:
    def __init__(self, status=200, text="", headers=None, url="", content=b""):
        self.status_code, self.text, self.headers, self.url, self.content = status, text, headers or {}, url, content


class Cookies(dict):
    def set(self, name, value, domain=None):
        self[name] = (value, domain)


class FakeClient:
    """routes by url substring; each route is a list of responses served in order."""

    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.calls = []
        self.s = type("S", (), {"cookies": Cookies()})()

    def get(self, url, **kw):
        self.calls.append((url, kw))
        for key, queue in self.routes.items():
            if key in url:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return Resp(404, url=url)


# ---------- urls ----------

@pytest.mark.parametrize("url,expected", [
    (f"https://www.tiktok.com/@scout2015/video/{VID}", (VID, False)),
    (f"https://www.tiktok.com/@scout2015/video/{VID}?is_from_webapp=1&sender_device=pc", (VID, False)),
    (f"tiktok.com/@a.b_c/video/{VID}", (VID, False)),
    (f"https://m.tiktok.com/v/{VID}.html", (VID, False)),
    (f"https://www.tiktok.com/embed/v2/{VID}", (VID, False)),
    (f"https://www.tiktok.com/embed/{VID}", (VID, False)),
    (f"https://www.tiktok.com/share/video/{VID}", (VID, False)),
    ("https://vm.tiktok.com/ZMabc123/", (None, True)),
    ("https://vt.tiktok.com/ZSabc123/", (None, True)),
    ("https://www.tiktok.com/t/ZTabc123/", (None, True)),
])
def test_parse_tiktok_url(url, expected):
    assert tiktok.is_tiktok_url(url)
    assert tiktok.parse_tiktok_url(url) == expected


def test_parse_rejects_photos_and_non_videos():
    with pytest.raises(tiktok.UnsupportedMedia):
        tiktok.parse_tiktok_url(f"https://www.tiktok.com/@someone/photo/{VID}")
    with pytest.raises(ExtractionError):
        tiktok.parse_tiktok_url("https://www.tiktok.com/@someone")
    assert not tiktok.is_tiktok_url("https://www.instagram.com/reel/abc/")
    assert not tiktok.is_tiktok_url("https://nottiktok.com/@a/video/1")


# ---------- page parsing + stream choice ----------

def test_parse_page_builds_media():
    m = tiktok.parse_page(FIXTURE, VID)
    assert (m.shortcode, m.username, m.source) == (VID, "scout2015", "tiktok_web")
    assert m.caption.startswith("Scramble")
    [item] = m.videos
    [v] = item.variants
    # 720p h265 beats 540p h264: resolution first
    assert (v.width, v.height) == (720, 1280) and "adapt_lower_720_1/" in v.url
    assert item.http_headers == TT_REFERER
    assert item.thumbnail.startswith("https://p16-sign.example.tiktokcdn-us.com/")
    assert item.duration and not item.dash_manifest


def test_pick_variant_rules():
    video = json.loads(tiktok.REHYDRATION_RE.search(FIXTURE).group(1))[
        "__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]["video"]
    # without the 720p entry: equal resolution -> h264 wins, then highest bitrate
    video["bitrateInfo"] = [b for b in video["bitrateInfo"] if b["GearName"] != "adapt_lower_720_1"]
    v, codec = tiktok.pick_variant(video)
    assert codec == "h264" and "/normal_540_0/" in v.url
    # bytevc2 is never picked even when it's the biggest
    video["bitrateInfo"].append({"GearName": "x", "CodecType": "bytevc2", "Bitrate": 9e9,
                                 "PlayAddr": {"Width": 2160, "Height": 3840, "UrlKey": "x_bytevc2_2160p",
                                              "UrlList": ["https://v16.example.tiktokcdn.com/vc2.mp4"]}})
    assert "vc2" not in tiktok.pick_variant(video)[0].url
    # no bitrateInfo (older videos) -> playAddr; the watermarked downloadAddr is never used
    video.pop("bitrateInfo")
    v, codec = tiktok.pick_variant(video)
    assert "/playaddr/" in v.url and codec == "h264"
    video.pop("playAddr")
    with pytest.raises(ExtractionError):
        tiktok.pick_variant(video)


def test_status_code_and_photo_errors():
    def private(vd):
        vd["statusCode"] = 10222
        vd["statusMsg"] = "author_secret"
        vd.pop("itemInfo")
    with pytest.raises(ExtractionError, match="author_secret"):
        tiktok.parse_page(_page(private), VID)

    def photo(vd):
        vd["itemInfo"]["itemStruct"]["imagePost"] = {"images": []}
    with pytest.raises(tiktok.UnsupportedMedia):
        tiktok.parse_page(_page(photo), VID)


# ---------- bot-check challenge ----------

def _challenge_page(answer=4242):
    prefix = b"reelgrab-prefix-"
    challenge = {"v": {"a": base64.b64encode(prefix).decode(),
                       "c": base64.b64encode(hashlib.sha256(prefix + str(answer).encode()).digest()).decode()},
                 "s": "keep-me"}
    cs = base64.b64encode(json.dumps(challenge).encode()).decode().rstrip("=")
    return (f'<html><body>Please wait...<p id="cs" class="{cs}"></p><p class="waf_ck" id="wci"></p>'
            f'<p id="rci" class="waf_rid"></p><p id="rs" class="rid-value"></p></body></html>')


def test_solve_waf():
    cookies = tiktok.solve_waf(_challenge_page(4242))
    assert cookies["waf_rid"] == "rid-value"
    solved = json.loads(base64.b64decode(cookies["waf_ck"]))
    assert base64.b64decode(solved["d"]) == b"4242" and solved["s"] == "keep-me"
    with pytest.raises(ExtractionError):
        tiktok.solve_waf("<html>nothing here</html>")


# ---------- extract ----------

def test_extract_happy_path_uses_canonical_page():
    c = FakeClient({f"/video/{VID}": Resp(200, FIXTURE)})
    m = tiktok.extract(f"https://www.tiktok.com/@scout2015/video/{VID}", c)
    assert m.username == "scout2015"
    assert c.calls[0][0] == f"https://www.tiktok.com/@_/video/{VID}"


def test_extract_solves_challenge_then_retries():
    c = FakeClient({f"/video/{VID}": [Resp(200, _challenge_page(7)), Resp(200, FIXTURE)]})
    m = tiktok.extract(f"https://www.tiktok.com/@scout2015/video/{VID}", c)
    assert m.shortcode == VID and "waf_ck" in c.s.cookies and len(c.calls) == 2


def test_extract_gives_up_when_challenge_repeats():
    c = FakeClient({f"/video/{VID}": Resp(200, _challenge_page(7))})
    with pytest.raises(ExtractionError, match="bot check"):
        tiktok.extract(f"https://www.tiktok.com/@scout2015/video/{VID}", c)


def test_short_links():
    good = FakeClient({"vm.tiktok.com": Resp(302, headers={"location": f"https://www.tiktok.com/@scout2015/video/{VID}?_r=1"}),
                       f"/video/{VID}": Resp(200, FIXTURE)})
    assert tiktok.extract("https://vm.tiktok.com/ZMabc123/", good).shortcode == VID
    home = FakeClient({"vm.tiktok.com": Resp(302, headers={"location": "https://www.tiktok.com/?_r=1"})})
    with pytest.raises(ExtractionError, match="doesn't point to a video"):
        tiktok.extract("https://vm.tiktok.com/ZMdead00/", home)


def test_extractor_dispatches_tiktok(monkeypatch):
    from reelgrab import extractor
    seen = []
    monkeypatch.setattr(tiktok, "extract", lambda url, client: seen.append(url) or "media")
    assert extractor.extract(f"https://www.tiktok.com/@a/video/{VID}", client=object()) == "media"
    assert seen


# ---------- download plumbing ----------

def test_plan_and_fetch_use_item_headers(tmp_path):
    from reelgrab.downloader import fetch, plan_for
    m = tiktok.parse_page(FIXTURE, VID)
    plan = plan_for(m.videos[0], "best")
    assert plan.mode == "progressive" and plan.http_headers == TT_REFERER
    c = FakeClient({"tiktokcdn.com": Resp(200, content=b"")})
    c.routes["tiktokcdn.com"][0].iter_content = lambda n: iter([b"abc"])
    c.routes["tiktokcdn.com"][0].close = lambda: None
    fetch(c, plan.video_url, str(tmp_path / "x.mp4"), headers=plan.http_headers)
    assert c.calls[0][1]["headers"]["Referer"] == "https://www.tiktok.com/"
    assert "Origin" not in c.calls[0][1]["headers"]
    assert (tmp_path / "x.mp4").read_bytes() == b"abc"


def test_web_thumb_proxy_allows_tiktok_cdn_only(monkeypatch):
    from fastapi.testclient import TestClient
    from reelgrab import web
    c = FakeClient({"tiktokcdn-us.com": Resp(200, content=b"jpg", headers={"content-type": "image/jpeg"})})
    monkeypatch.setattr(web, "_client", c)
    api = TestClient(web.app)
    ok = api.get("/api/thumb", params={"u": "https://p16-sign.example.tiktokcdn-us.com/cover.jpeg?x=1"})
    assert ok.status_code == 200 and c.calls[0][1]["headers"]["Referer"] == "https://www.tiktok.com/"
    assert api.get("/api/thumb", params={"u": "https://evil.example.com/tiktokcdn.com.jpg"}).status_code == 400


# ---------- live (opt-in) ----------

@pytest.mark.skipif(not os.environ.get("REELGRAB_LIVE") or not shutil.which("ffprobe"), reason="set REELGRAB_LIVE=1")
def test_live_download(tmp_path):
    from reelgrab.client import IGClient
    from reelgrab.downloader import download
    c = IGClient()
    m = tiktok.extract(f"https://www.tiktok.com/@scout2015/video/{VID}", c)
    [path] = download(m, c, str(tmp_path))
    codecs = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                            capture_output=True, text=True).stdout.split()
    assert codecs[:2] == ["h264", "aac"] or sorted(codecs) == ["aac", "h264"]
