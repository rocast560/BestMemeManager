# reelgrab

Local Instagram Reels to MP4 converter. Paste a reel link, get the MP4. Web UI + CLI, runs in Docker, no third-party download sites involved. The extraction logic is written from scratch (no yt-dlp / instaloader dependency), based on how those projects talk to Instagram today.

## Quick start (Docker)

```powershell
cd reelgrab
docker compose up -d --build
```

Open http://localhost:8080, paste a link, hit Convert, then Download MP4. Every file also gets saved to `./downloads`.

CLI through the same image:

```powershell
docker compose run --rm reelgrab reelgrab -o /downloads https://www.instagram.com/reel/XXXXXXXXXXX/
```

## Without Docker

Needs Python 3.10+ and ffmpeg on PATH (ffmpeg is only needed for best-quality mode).

```bash
pip install -e .[dev]
reelgrab https://www.instagram.com/reel/XXXXXXXXXXX/          # download to cwd
reelgrab -q progressive -o out/ <url1> <url2>                 # skip ffmpeg, grab single mp4
reelgrab --info <url>                                         # dump metadata + chosen tracks as json
uvicorn reelgrab.web:app --port 8080                          # web ui
pytest                                                        # offline test suite
```

Accepted inputs: `/reel/`, `/reels/`, `/p/`, `/tv/`, `/<user>/reel/`, `/share/reel/` links, and bare shortcodes.

## Meme archive (Windows)

A desktop app for keeping clips in folders and getting them into Discord fast. It reads the same `downloads/` folder the Docker web UI saves to.

```powershell
start-archive.bat        # first run creates .venv and installs PySide6
```

- **Save:** paste a reel link in the top box. It lands in **Inbox**. You can also drop mp4s from Explorer onto the window.
- **Organize:** drag clips onto folders. Right-click the tree for New folder / Rename / Delete. Deletes go to `downloads/.reelgrab/trash` and Ctrl+Z undoes them.
- **Send:** select clips and press **Ctrl+C**, then **Ctrl+V** in Discord. Or drag a tile straight into Discord.
- **Quick-picker:** **Ctrl+Shift+M** from anywhere, type, press **Enter**, and the clip is pasted into the window you were in. **Shift+Enter** only copies. Closing the window keeps the app in the tray so the hotkey keeps working.
- **Keys in the grid:** F favorite, T tags, F2 rename, Del delete, Enter open, Ctrl+F search.
- **Phone playback:** every clip is converted to H.264/AAC (Instagram's VP9 streams don't play on iOS). Clips over 10 MB get a shrunk copy in `.reelgrab/send/`, and that copy is what Discord receives. Originals are never touched.
- **Settings (⚙):** hotkey, size limit (raise it if you have Nitro), auto-paste on/off, archive folder. They're stored in `%APPDATA%\reelgrab\settings.json`. `REELGRAB_ARCHIVE_DIR` overrides the folder.

Manual check after changes (these can't be automated):
1. Ctrl+C a clip → Ctrl+V in Discord uploads it.
2. Dragging a tile into Discord uploads it. Dragging onto the desktop copies it and leaves the archive file in place.
3. Ctrl+Shift+M in Discord → type → Enter puts the clip in the message box.
4. A clip over 10 MB arrives under 10 MB and plays in the Discord phone app.
5. With the window closed, the hotkey still opens the picker.

## How it works

**1. Link to media ID.** A reel URL like `/reel/DAbc123XyZ_/` holds a *shortcode*, which is the post's numeric media ID (`pk`) written in URL-safe base64 (`A-Z a-z 0-9 - _`). `shortcode.py` decodes it. `/share/` links are resolved by following their redirect first. Private-post shortcodes carry 28 extra trailing chars that get trimmed.

**2. Look like a browser.** Instagram fingerprints the TLS handshake and HTTP/2 settings, and plain `requests` gets login walls much more often. The client uses `curl_cffi` to replay Chrome's fingerprint, then loads the homepage once to collect:
- the `csrftoken` cookie (sent back as `X-CSRFToken`)
- the `LSD` token (from the `__eqmc` script or the `["LSD",[],{"token":...}]` config), which Relay GraphQL calls need

Every API call also carries the web app headers: `X-IG-App-ID: 936619743392459`, `X-ASBD-ID`, `X-IG-WWW-Claim: 0`.

**3. Ask for the media, falling back through strategies** (`extractor.py`):

| # | Strategy | Request | Where the video is |
|---|----------|---------|--------------------|
| 0 | v1 API (only with a session cookie) | `GET /api/v1/media/{pk}/info/` | `items[0]` |
| 1 | Polaris GraphQL (yt-dlp's method) | `POST /api/graphql`, `doc_id=27130156389949648`, `variables={"media_id": pk}` | `data.xig_polaris_media.if_not_gated_logged_out` |
| 2 | web_info GraphQL (instaloader's method) | `POST /graphql/query`, `doc_id=27128499623469141`, `variables={"shortcode": ...}` | `data.xdt_api__v1__media__shortcode__web_info.items[0]` |
| 3 | Post page scrape | `GET /p/{code}/` | Relay prefetch JSON in `<script type="application/json" data-sjs>` |
| 4 | Embed page scrape | `GET /p/{code}/embed/captioned/` | `video_url` inside the (escaped) `contextJSON` blob |

Before that it calls `/api/v1/web/get_ruling_for_content/`, which is Instagram's own "can a logged-out user see this" check, so failures come back with a readable reason (e.g. age-restricted).

**4. Pick the best file.** The media object has two kinds of video:
- `video_versions`: progressive MP4s with audio already muxed in. Ready to save as-is.
- `video_dash_manifest`: an inline MPEG-DASH XML with separate video-only and audio-only MP4s (one `BaseURL` per track, no segments). Often higher resolution.

In `best` mode (default) it parses the MPD, and if the top DASH video track beats the best progressive file, it downloads that plus the best audio track and muxes them with `ffmpeg -c copy` (no re-encode, instant). Otherwise it just saves the progressive MP4. Carousels with multiple videos produce one file each (zipped in the web UI).

**5. Download.** CDN URLs (`*.cdninstagram.com`, `*.fbcdn.net`) are signed and expire after a while, so they're fetched right after extraction with an Instagram `Referer`.

## Config

Copy `.env.example` to `.env` (compose picks it up automatically).

| Var | Purpose |
|-----|---------|
| `IG_SESSIONID` | `sessionid` cookie from a logged-in browser. Unlocks strategy 0 and gets past anonymous rate limits. Use a throwaway account. |
| `IG_COOKIES_FILE` | Path to a Netscape `cookies.txt` instead (drop it in `./downloads` and use `/downloads/cookies.txt`). |
| `REELGRAB_POLARIS_DOC_ID` / `REELGRAB_WEBINFO_DOC_ID` | Override GraphQL doc IDs when Instagram rotates them. |
| `REELGRAB_IMPERSONATE` | curl_cffi browser target (default `chrome`). |

## When it breaks

Instagram changes this stuff every few months. Run with `-v` to see which strategies failed and why.

- **"empty response" from both GraphQL strategies:** doc IDs rotated. Open any reel logged out in Chrome, DevTools > Network, filter `graphql`, find the request whose response has `xig_polaris_media` or `shortcode__web_info`, copy its `doc_id` into `.env`. Checking the latest yt-dlp `extractor/instagram.py` or instaloader `structures.py` also works.
- **Redirected to login / everything empty:** you're rate-limited as an anonymous client. Wait a bit or set `IG_SESSIONID`.
- **CDN http 403:** the signed URL expired between extract and download. Just retry.

## Layout

```
reelgrab/
  shortcode.py   url parsing, shortcode <-> media id
  client.py      curl_cffi session, csrf + lsd bootstrap, cookies
  extractor.py   the 5 strategies + normalizing responses
  dash.py        MPD parsing, best video/audio pick
  downloader.py  quality plan, streaming download, ffmpeg mux
  cli.py         `reelgrab` command
  web.py         FastAPI app (/api/info, /api/download, /api/thumb)
  static/        web ui
tests/           offline tests with mocked instagram responses
```

Only download content you have the right to; reposting other people's reels can break Instagram's terms and copyright.
