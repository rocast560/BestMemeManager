from dataclasses import dataclass, field, asdict


@dataclass
class VideoVariant:
    url: str
    width: int | None = None
    height: int | None = None

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)


@dataclass
class VideoItem:
    variants: list[VideoVariant] = field(default_factory=list)  # progressive mp4s (video+audio muxed)
    dash_manifest: str | None = None  # separate video/audio tracks, sometimes higher res
    duration: float | None = None
    thumbnail: str | None = None
    has_audio: bool | None = None

    def best_variant(self) -> VideoVariant | None:
        return max(self.variants, key=lambda v: v.pixels, default=None)


@dataclass
class Media:
    shortcode: str
    username: str | None = None
    caption: str | None = None
    videos: list[VideoItem] = field(default_factory=list)
    source: str = ""  # which strategy produced this

    def to_dict(self) -> dict:
        d = asdict(self)
        for v in d["videos"]:
            v.pop("dash_manifest", None)
        for v, item in zip(d["videos"], self.videos):
            v["has_dash"] = bool(item.dash_manifest)
        return d
