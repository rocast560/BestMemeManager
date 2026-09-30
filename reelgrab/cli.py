import argparse
import json
import sys

from .client import IGClient
from .downloader import download, plan_for
from .extractor import ExtractionError, extract
from .shortcode import InvalidReelUrl


def _bar(done: int, total: int | None) -> None:
    if total:
        pct = done * 100 // total
        sys.stderr.write(f"\r  {pct:3d}%  {done / 1e6:6.1f} / {total / 1e6:.1f} MB")
    else:
        sys.stderr.write(f"\r  {done / 1e6:6.1f} MB")
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reelgrab", description="download instagram reels as mp4")
    p.add_argument("urls", nargs="+", help="reel/post urls (or bare shortcodes)")
    p.add_argument("-o", "--out", default=".", help="output directory (default: .)")
    p.add_argument("-q", "--quality", choices=["best", "progressive"], default="best",
                   help="best = mux dash tracks with ffmpeg when higher res, progressive = single mp4 as-is")
    p.add_argument("--cookies", help="netscape cookies.txt exported from a logged-in browser")
    p.add_argument("--sessionid", help="instagram sessionid cookie value (or set IG_SESSIONID)")
    p.add_argument("--info", action="store_true", help="print metadata + direct urls as json, don't download")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    client = IGClient(cookies_file=args.cookies, sessionid=args.sessionid)
    failed = 0
    for url in args.urls:
        try:
            media = extract(url, client, verbose=args.verbose)
            if args.info:
                d = media.to_dict()
                d["plans"] = [plan_for(v, args.quality).__dict__ for v in media.videos]
                print(json.dumps(d, indent=2))
                continue
            print(f"[{media.source}] @{media.username or '?'} {media.shortcode}", file=sys.stderr)
            for path in download(media, client, args.out, args.quality, _bar):
                sys.stderr.write("\n")
                print(path)
        except (InvalidReelUrl, ExtractionError, RuntimeError) as e:
            failed += 1
            print(f"error: {url}\n{e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
