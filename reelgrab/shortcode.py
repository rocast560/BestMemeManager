import re
from urllib.parse import urlparse

# instagram shortcodes are just the media pk encoded in url-safe base64
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

_PATH_RE = re.compile(r"^/(?:[\w.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
_SHARE_RE = re.compile(r"^/share/(?:reel/|p/)?([A-Za-z0-9_-]+)")
_BARE_RE = re.compile(r"^[A-Za-z0-9_-]{5,}$")


class InvalidReelUrl(ValueError):
    pass


def parse_url(url: str) -> tuple[str, bool]:
    """returns (code, is_share_link). share links need a redirect hop to get the real shortcode."""
    url = url.strip()
    if _BARE_RE.match(url):
        return url, False
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am"):
        raise InvalidReelUrl(f"not an Instagram or TikTok link: {url}")

    if m := _SHARE_RE.match(parsed.path):
        return m.group(1), True
    if m := _PATH_RE.match(parsed.path):
        return m.group(1), False
    raise InvalidReelUrl(f"couldn't find a reel/post shortcode in: {url}")


def shortcode_to_pk(shortcode: str) -> int:
    # private posts tack 28 extra chars onto the real shortcode
    if len(shortcode) > 28:
        shortcode = shortcode[:-28]
    pk = 0
    for ch in shortcode:
        pk = pk * 64 + ALPHABET.index(ch)
    return pk


def pk_to_shortcode(pk: int) -> str:
    out = ""
    while pk:
        pk, rem = divmod(pk, 64)
        out = ALPHABET[rem] + out
    return out or "A"
