import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from reelgrab import extractor as ex
from reelgrab.client import IGClient
from reelgrab.dash import best_pair, parse_mpd
from reelgrab.downloader import plan_for
from reelgrab.shortcode import InvalidReelUrl, parse_url, pk_to_shortcode, shortcode_to_pk

SC = "DAbc123XyZ_"
MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
 <AdaptationSet contentType="video" mimeType="video/mp4">
  <Representation id="v1" bandwidth="900000" width="720" height="1280" codecs="avc1"><BaseURL>https://scontent.cdninstagram.com/v720.mp4</BaseURL></Representation>
  <Representation id="v2" bandwidth="3000000" width="1080" height="1920" codecs="avc1"><BaseURL>https://scontent.cdninstagram.com/v1080.mp4</BaseURL></Representation>
 </AdaptationSet>
 <AdaptationSet contentType="audio" mimeType="audio/mp4">
  <Representation id="a1" bandwidth="64000"><BaseURL>https://scontent.cdninstagram.com/a64.mp4</BaseURL></Representation>
  <Representation id="a2" bandwidth="128000"><BaseURL>https://scontent.cdninstagram.com/a128.mp4</BaseURL></Representation>
 </AdaptationSet>
</Period></MPD>"""

V1_ITEM = {
    "code": SC,
    "user": {"username": "someone"},
    "caption": {"text": "hello"},
    "video_duration": 12.3,
    "has_audio": True,
    "video_versions": [
        {"url": "https://scontent.cdninstagram.com/p480.mp4", "width": 480, "height": 854},
        {"url": "https://scontent.cdninstagram.com/p720.mp4", "width": 720, "height": 1280},
    ],
    "video_dash_manifest": MPD,
    "image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/t.jpg"}]},
}


class Resp(SimpleNamespace):
    pass


def resp(text="", status=200, url="https://www.instagram.com/"):
    return Resp(text=text, status_code=status, url=url, headers={})


class FakeClient(IGClient):
    """routes requests to canned responses so tests never touch the network."""

    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.calls = []

    def bootstrap(self):
        self.lsd, self.csrf = "LSDTOKEN", "CSRF"

    def resolve_share(self, code):
        return f"https://www.instagram.com/reel/{SC}/"

    def _route(self, method, url):
        self.calls.append((method, url))
        for key, val in self.routes.items():
            if key in url:
                return val() if callable(val) else val
        return resp("{}", 404, url)

    def get(self, url, **kw):
        return self._route("GET", url)

    def post(self, url, **kw):
        self.calls.append(("POST-DATA", kw.get("data")))
        return self._route("POST", url)

    def polite_sleep(self, seconds=0):
        pass


# ---------- shortcode ----------

@pytest.mark.parametrize("url,code,share", [
    (f"https://www.instagram.com/reel/{SC}/", SC, False),
    (f"https://instagram.com/reels/{SC}/?igsh=abc", SC, False),
    (f"https://www.instagram.com/p/{SC}/", SC, False),
    (f"https://www.instagram.com/someuser/reel/{SC}/", SC, False),
    (f"instagram.com/tv/{SC}", SC, False),
    ("https://www.instagram.com/share/reel/BAxyz123/", "BAxyz123", True),
    (SC, SC, False),
])
def test_parse_url(url, code, share):
    assert parse_url(url) == (code, share)


@pytest.mark.parametrize("bad", ["https://youtube.com/watch?v=x", "https://www.instagram.com/someuser/", "hi there"])
def test_parse_url_rejects(bad):
    with pytest.raises(InvalidReelUrl):
        parse_url(bad)


def test_shortcode_roundtrip():
    for pk in (1, 63, 64, 3_141_592_653_589_793_238, 2_934_812_734_986_123_456):
        assert shortcode_to_pk(pk_to_shortcode(pk)) == pk


def test_known_shortcode():
    # pair from yt-dlp's test suite: instagram://media?id=482584233761418119 -> /p/aye83DjauH/
    assert shortcode_to_pk("aye83DjauH") == 482584233761418119
    assert pk_to_shortcode(482584233761418119) == "aye83DjauH"


def test_private_shortcode_is_trimmed():
    assert shortcode_to_pk(SC + "x" * 28) == shortcode_to_pk(SC)


# ---------- dash ----------

def test_mpd_best_pair():
    v, a = best_pair(parse_mpd(MPD))
    assert v.url.endswith("v1080.mp4") and (v.width, v.height) == (1080, 1920)
    assert a.url.endswith("a128.mp4")


# ---------- normalize + plan ----------

def test_normalize_v1_and_plan(monkeypatch):
    m = ex.normalize(V1_ITEM, SC, "test")
    assert m.username == "someone" and m.caption == "hello" and len(m.videos) == 1
    assert m.videos[0].best_variant().height == 1280

    monkeypatch.setattr("reelgrab.downloader.ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    p = plan_for(m.videos[0], "best")
    assert p.mode == "dash" and p.height == 1920 and p.audio_url.endswith("a128.mp4")
    assert plan_for(m.videos[0], "progressive").mode == "progressive"

    monkeypatch.setattr("reelgrab.downloader.ffmpeg_path", lambda: None)
    assert plan_for(m.videos[0], "best").mode == "progressive"


def test_normalize_carousel_skips_images():
    node = {"code": SC, "user": {"username": "u"}, "carousel_media": [
        {"image_versions2": {"candidates": [{"url": "x"}]}},
        {"video_versions": [{"url": "https://a/1.mp4", "width": 1, "height": 1}]},
        {"video_versions": [{"url": "https://a/2.mp4", "width": 1, "height": 1}]},
    ]}
    assert len(ex.normalize(node, SC, "t").videos) == 2


def test_normalize_legacy_graphql_shape():
    node = {"shortcode": SC, "owner": {"username": "legacy"}, "video_url": "https://a/v.mp4",
            "dimensions": {"width": 640, "height": 1136},
            "edge_media_to_caption": {"edges": [{"node": {"text": "cap"}}]}}
    m = ex.normalize(node, SC, "t")
    assert m.username == "legacy" and m.caption == "cap" and m.videos[0].variants[0].height == 1136


# ---------- strategies end to end (mocked network) ----------

def polaris_ok():
    return resp(json.dumps({"data": {"xig_polaris_media": {"if_not_gated_logged_out": V1_ITEM}}}))


def test_extract_polaris_first():
    c = FakeClient({"get_ruling_for_content": resp('{"status":"ok"}'), "/api/graphql": polaris_ok})
    m = ex.extract(f"https://www.instagram.com/reel/{SC}/", c)
    assert m.source == "polaris_graphql"
    data = next(d for kind, d in c.calls if kind == "POST-DATA")
    assert data["doc_id"] == ex.POLARIS_DOC_ID and data["lsd"] == "LSDTOKEN"
    assert json.loads(data["variables"]) == {"media_id": str(shortcode_to_pk(SC))}


def test_extract_falls_back_to_webinfo():
    body = {"data": {"xdt_api__v1__media__shortcode__web_info": {"items": [V1_ITEM]}}}
    c = FakeClient({
        "get_ruling_for_content": resp('{"status":"ok"}'),
        "/api/graphql": resp('{"data":{"xig_polaris_media":null}}'),
        "/graphql/query": resp("for (;;);" + json.dumps(body)),
    })
    assert ex.extract(SC, c).source == "webinfo_graphql"


def test_extract_falls_back_to_post_page_relay_json():
    blob = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [
        ["RelayPrefetchedStreamCache", "next", [], ["key", {"__bbox": {"result": {"data": {
            "xig_polaris_media": {"if_not_gated_logged_out": V1_ITEM}}}}}]]]}}]]]}
    page = f'<html><script type="application/json" data-sjs>{json.dumps(blob)}</script></html>'
    c = FakeClient({
        "get_ruling_for_content": resp('{"status":"ok"}'),
        "/api/graphql": resp("{}", 500),
        "/graphql/query": resp("<html>blocked</html>", 200),
        f"/p/{SC}/embed": resp("", 404),
        f"/p/{SC}/": resp(page, url=f"https://www.instagram.com/p/{SC}/"),
    })
    m = ex.extract(SC, c)
    assert m.source == "post_page" and m.username == "someone"


def test_extract_falls_back_to_embed_escaped_video_url():
    ctx = json.dumps({"context": {"media": {"shortcode": SC, "video_url": "https://scontent.cdninstagram.com/e.mp4?a=1&b=2"}}})
    embed = '<script>window.__x = {"contextJSON":' + json.dumps(ctx) + "};</script>"
    c = FakeClient({
        "get_ruling_for_content": resp('{"status":"ok"}'),
        "/api/graphql": resp("{}", 429),
        "/graphql/query": resp("{}", 429),
        f"/p/{SC}/embed": resp(embed),
        f"/p/{SC}/": resp("", url="https://www.instagram.com/accounts/login/"),
    })
    m = ex.extract(SC, c)
    assert m.source == "embed_page"
    assert m.videos[0].variants[0].url == "https://scontent.cdninstagram.com/e.mp4?a=1&b=2"


def test_extract_all_fail_reports_ruling():
    c = FakeClient({
        "get_ruling_for_content": resp('{"status":"fail","title":"Restricted Video","description":"18+"}'),
    })
    with pytest.raises(ex.ExtractionError) as e:
        ex.extract(SC, c)
    msg = str(e.value)
    assert "Restricted Video" in msg and "polaris_graphql" in msg and "embed_page" in msg


def test_share_link_resolves():
    c = FakeClient({"get_ruling_for_content": resp('{"status":"ok"}'), "/api/graphql": polaris_ok})
    assert ex.extract("https://www.instagram.com/share/reel/BAxyz123/", c).shortcode == SC


# ---------- ffmpeg mux ----------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_mux(tmp_path):
    from reelgrab.downloader import mux
    v, a, out = tmp_path / "v.mp4", tmp_path / "a.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=108x192:rate=10",
                    "-t", "1", "-an", "-c:v", "libx264", str(v)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "1", "-vn", "-c:a", "aac", str(a)], check=True)
    mux(str(v), str(a), str(out))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert sorted(probe) == ["audio", "video"]


# ---------- web api ----------

def test_web_info_and_guards(monkeypatch):
    from fastapi.testclient import TestClient
    from reelgrab import web

    fake = FakeClient({"get_ruling_for_content": resp('{"status":"ok"}'), "/api/graphql": polaris_ok})
    monkeypatch.setattr(web, "_client", fake)
    api = TestClient(web.app)

    assert api.get("/").status_code == 200
    assert api.get("/healthz").json()["ok"] is True

    j = api.get("/api/info", params={"url": f"https://www.instagram.com/reel/{SC}/"}).json()
    assert j["username"] == "someone" and j["source"] == "polaris_graphql"
    assert j["plans"][0]["video_url"] is None  # don't leak cdn urls through info, download goes via server

    assert api.get("/api/info", params={"url": "https://example.com/x"}).status_code == 400
    assert api.get("/api/thumb", params={"u": "https://evil.example.com/a.jpg"}).status_code == 400


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_make_compatible_reencodes_vp9(tmp_path):
    from reelgrab.downloader import make_compatible, probe_codecs
    f = tmp_path / "vp9.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=108x192:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1",
                    "-c:v", "libvpx-vp9", "-c:a", "aac", str(f)], check=True)
    assert probe_codecs(str(f))["video"] == "vp9"
    assert make_compatible(str(f)) is True
    assert probe_codecs(str(f)) == {"video": "h264", "audio": "aac"}
    assert make_compatible(str(f)) is False  # already fine, left alone
