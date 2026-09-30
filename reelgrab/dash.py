"""picks the best video + audio tracks out of instagram's inline DASH manifest.

reels ship two ways: a progressive mp4 (video_versions, already has audio) and a DASH
manifest with separate video-only and audio-only mp4s. DASH is sometimes a higher
resolution/bitrate, so in 'best' mode we grab both tracks and mux them with ffmpeg.
each Representation is a single whole-file BaseURL (SegmentBase), so no segment stitching.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Track:
    url: str
    kind: str  # "video" | "audio"
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    codecs: str = ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _int(v: str | None) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def parse_mpd(xml_text: str) -> list[Track]:
    root = ET.fromstring(xml_text)
    tracks = []
    for aset in root.iter():
        if _local(aset.tag) != "AdaptationSet":
            continue
        set_kind = aset.get("contentType") or (aset.get("mimeType") or "").split("/")[0]
        for rep in aset:
            if _local(rep.tag) != "Representation":
                continue
            kind = set_kind or (rep.get("mimeType") or "").split("/")[0]
            base = next((el.text for el in rep if _local(el.tag) == "BaseURL" and el.text), None)
            if kind not in ("video", "audio") or not base or not base.strip().startswith("http"):
                continue
            tracks.append(Track(
                url=base.strip(),
                kind=kind,
                bandwidth=_int(rep.get("bandwidth")),
                width=_int(rep.get("width") or aset.get("width")),
                height=_int(rep.get("height") or aset.get("height")),
                codecs=rep.get("codecs") or "",
            ))
    return tracks


def best_pair(tracks: list[Track]) -> tuple[Track | None, Track | None]:
    videos = [t for t in tracks if t.kind == "video"]
    audios = [t for t in tracks if t.kind == "audio"]
    # at equal resolution prefer h264: instagram's vp9 tracks don't play on ios
    v = max(videos, key=lambda t: (t.width * t.height, t.codecs.startswith("avc1"), t.bandwidth), default=None)
    a = max(audios, key=lambda t: t.bandwidth, default=None)
    return v, a
