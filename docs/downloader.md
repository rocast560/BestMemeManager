# The downloader: setup, config, and when it breaks

## Docker

```powershell
docker compose up -d --build
```

Open http://localhost:8080, paste a link, hit Convert, then Download MP4. Every file also lands in `./downloads`.

The command line works through the same image:

```powershell
docker compose run --rm reelgrab reelgrab -o /downloads https://www.instagram.com/reel/XXXXXXXXXXX/
```

## Without Docker

Needs Python 3.10+ and ffmpeg on PATH.

```bash
pip install -e .[dev]
reelgrab https://www.instagram.com/reel/XXXXXXXXXXX/          # download to the current folder
reelgrab https://www.tiktok.com/@user/video/1234567890        # tiktok works the same way
reelgrab -q progressive -o out/ <url1> <url2>                 # skip the DASH mux, grab the single mp4
reelgrab --info <url>                                         # print metadata + chosen tracks as json
uvicorn reelgrab.web:app --port 8080                          # web ui
pytest                                                        # offline test suite
```

Instagram links that work: `/reel/`, `/reels/`, `/p/`, `/tv/`, `/<user>/reel/`, `/share/reel/`, and bare shortcodes. TikTok links that work: `/@user/video/<id>`, embed links, and the short `vm.tiktok.com`, `vt.tiktok.com` and `tiktok.com/t/` links. TikTok photo slideshows don't.

## Config

Copy `.env.example` to `.env` (compose picks it up on its own, and the archive app reads it too).

| Var | What it's for |
|-----|---------|
| `IG_SESSIONID` | The `sessionid` cookie from a logged-in browser. Turns on the v1 API strategy and gets you past anonymous rate limits. Use a throwaway account. |
| `IG_COOKIES_FILE` | Path to a Netscape `cookies.txt` instead (drop it in `./downloads` and use `/downloads/cookies.txt`). |
| `REELGRAB_POLARIS_DOC_ID` / `REELGRAB_WEBINFO_DOC_ID` | Override the GraphQL doc IDs when Instagram rotates them. |
| `REELGRAB_IMPERSONATE` | curl_cffi browser target (default `chrome`). |
| `REELGRAB_ARCHIVE_DIR` | Where the archive app keeps clips (default `./downloads`). |

## When it breaks

Instagram and TikTok both change this stuff every few months. Run the CLI with `-v` to see which Instagram strategies failed and why.

- "empty response" from both GraphQL strategies means the doc IDs rotated. Open any reel logged out in Chrome, go to DevTools > Network, filter for `graphql`, find the request whose response has `xig_polaris_media` or `shortcode__web_info`, and copy its `doc_id` into `.env`. The latest yt-dlp `extractor/instagram.py` or instaloader `structures.py` will have them too.
- Redirects to login, or everything empty: you're rate-limited as an anonymous client. Wait a bit or set `IG_SESSIONID`.
- CDN http 403: the signed URL expired between finding it and downloading it. Retry.
- TikTok "no video data in the page": TikTok changed its page layout. Check whether yt-dlp's `extractor/tiktok.py` changed recently.
