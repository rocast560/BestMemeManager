import json
import os
import re
import time
from http.cookiejar import MozillaCookieJar

from curl_cffi import requests as creq

BASE = "https://www.instagram.com"
WEB_APP_ID = "936619743392459"
IMPERSONATE = os.environ.get("REELGRAB_IMPERSONATE", "chrome")

_LSD_RES = [
    re.compile(r'\["LSD",\[\],\{"token":"([^"]+)"'),
    re.compile(r'"lsd":\{"name":"lsd","value":"([^"]+)"'),
]
_EQMC_RE = re.compile(r'<script[^>]*id="__eqmc"[^>]*>(\{.*?\})</script>', re.S)
_CSRF_RE = re.compile(r'"csrf_token":"([^"]+)"')


class IGClient:
    """thin wrapper around a curl_cffi session that looks like desktop chrome to instagram.

    instagram fingerprints the tls handshake, so plain python-requests gets served
    empty/login-wall responses way more often. curl_cffi replays chrome's ja3/http2 fingerprint.
    """

    def __init__(self, cookies_file: str | None = None, sessionid: str | None = None, timeout: int = 20):
        self.s = creq.Session(impersonate=IMPERSONATE)
        self.timeout = timeout
        self.lsd: str | None = None
        self.csrf: str | None = None
        self._bootstrapped = False

        cookies_file = cookies_file or os.environ.get("IG_COOKIES_FILE")
        sessionid = sessionid or os.environ.get("IG_SESSIONID")
        if cookies_file and os.path.exists(cookies_file):
            jar = MozillaCookieJar(cookies_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            for c in jar:
                if "instagram.com" in c.domain:
                    self.s.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        if sessionid:
            self.s.cookies.set("sessionid", sessionid, domain=".instagram.com", path="/")

    @property
    def logged_in(self) -> bool:
        return bool(self._cookie("sessionid"))

    def _cookie(self, name: str) -> str | None:
        for c in self.s.cookies.jar:
            if c.name == name and c.value:
                return c.value
        return None

    def api_headers(self, referer: str = BASE + "/") -> dict:
        h = {
            "X-IG-App-ID": WEB_APP_ID,
            "X-ASBD-ID": "359341",
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE,
            "Referer": referer,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self.csrf:
            h["X-CSRFToken"] = self.csrf
        return h

    def bootstrap(self) -> None:
        """hit the homepage once to pick up the csrftoken cookie + the LSD token relay queries want."""
        if self._bootstrapped:
            return
        r = self.s.get(BASE + "/", timeout=self.timeout, headers={"Accept-Language": "en-US,en;q=0.9"})
        html = r.text

        if m := _EQMC_RE.search(html):
            try:
                self.lsd = json.loads(m.group(1)).get("l")
            except json.JSONDecodeError:
                pass
        if not self.lsd:
            for rx in _LSD_RES:
                if m := rx.search(html):
                    self.lsd = m.group(1)
                    break

        self.csrf = self._cookie("csrftoken")
        if not self.csrf and (m := _CSRF_RE.search(html)):
            self.csrf = m.group(1)
            self.s.cookies.set("csrftoken", self.csrf, domain=".instagram.com", path="/")
        self._bootstrapped = True

    def resolve_share(self, share_code: str) -> str:
        """/share/<id> links 301 to the real /reel/<shortcode>/ url."""
        r = self.s.get(f"{BASE}/share/reel/{share_code}/", timeout=self.timeout, allow_redirects=True)
        return str(r.url)

    def get(self, url: str, **kw):
        kw.setdefault("timeout", self.timeout)
        return self.s.get(url, **kw)

    def post(self, url: str, **kw):
        kw.setdefault("timeout", self.timeout)
        return self.s.post(url, **kw)

    def polite_sleep(self, seconds: float = 0.4) -> None:
        # tiny gap between strategies so we don't look like a burst
        time.sleep(seconds)
