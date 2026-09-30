(made by me)

# BestMemeManager

I keep a folder of reaction videos and memes for Discord, and I got tired of the loop: find the reel, download it somewhere, dig the file out of Downloads, drag it into Discord, then hear from the people on phones that it won't play. This repo fixes that loop. Paste an Instagram reel or TikTok link, get an mp4 that plays everywhere (including iPhones), keep it in folders, and get it into a Discord message with a hotkey.

There are two parts:

- A downloader (web page + command line, runs in Docker) that turns Instagram and TikTok links into mp4s. It talks to Instagram and TikTok directly. There's no yt-dlp dependency and no third-party download site in the middle.
- A Windows desktop app for the meme archive itself: folders, tags, favorites, search, and a tray panel next to the clock. Ctrl+C a clip and Ctrl+V it into Discord, or drag it in.

The Python package and commands are still called `reelgrab`, which was the project's first name.

## Why the files play on phones now

Instagram serves its highest-quality video as VP9 inside an .mp4 file. Desktop Chrome and desktop Discord play that fine. iOS doesn't, so Discord on an iPhone shows a dead video. Every download is now checked with ffprobe, and anything that isn't H.264 video with AAC audio gets re-encoded (CRF 18, so you won't see the difference). TikTok often sends H.265, which gets the same treatment.

Discord also caps free uploads at 10 MB. The archive app keeps your original and makes a shrunk H.264 copy for anything bigger, and that copy is what Discord gets. It keeps the clip's real filename, so people see `cat reacting.mp4` and not a hash.

## Getting started

### The meme archive (Windows)

You need Python 3.10+ and ffmpeg (`winget install Gyan.FFmpeg`). Then double-click `start-archive.bat`. The first run makes a `.venv`, installs PySide6, and puts a "reelgrab archive" shortcut on your Desktop and in the Start menu. Use the shortcut after that.

To have it start with Windows, open settings (the gear) and tick "Launch at Windows startup". It'll start hidden in the tray at login.

It reads and writes the `downloads/` folder, the same one the Docker downloader saves into, so anything you grab from the web page shows up in the app's Inbox.

### The downloader (Docker)

```powershell
docker compose up -d --build
```

Open http://localhost:8080, paste a link, hit Convert, then Download MP4. Every file also lands in `./downloads`.

The command line works through the same image:

```powershell
docker compose run --rm reelgrab reelgrab -o /downloads https://www.instagram.com/reel/XXXXXXXXXXX/
```

### Without Docker

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

Instagram links that work: `/reel/`, `/reels/`, `/p/`, `/tv/`, `/<user>/reel/`, `/share/reel/`, and bare shortcodes. TikTok links that work: `/@user/video/<id>`, embed links, and the short `vm.tiktok.com`, `vt.tiktok.com` and `tiktok.com/t/` links. TikTok photo slideshows don't work.

## Using the archive

The main window has a folder tree on the left (Favorites, Recent and Inbox are pinned at the top) and a grid of thumbnails that play when you hover over them. Paste a link in the top box and the clip downloads into Inbox. You can also drag mp4s in from Explorer. Drag clips onto a folder to move them, and right-click the tree to make, rename or delete folders.

Getting a clip into Discord:

- Select it and press Ctrl+C, then Ctrl+V in Discord. The app puts the file itself on the clipboard, same as copying it in Explorer.
- Drag the tile straight into Discord. Dropping it on your desktop makes a copy; the archive keeps its file.
- Press Ctrl+Shift+M from anywhere. A search box pops up, you type a few letters, press Enter, and the clip gets pasted into whatever window you were in. Shift+Enter copies without pasting.
- Left-click the tray icon (by the clock and wifi) for a smaller panel with search, Favorites / Recent / Folder tabs, and a link box. Click a clip to copy it. Right-click it to favorite, tag, move or delete it. Double-clicking the tray icon opens the full window.

Closing the main window doesn't quit the app, so the hotkey and tray panel keep working. Quit from the tray menu.

| Key (in the grid) | Does |
|---|---|
| Ctrl+C | copy for Discord |
| F | favorite / unfavorite |
| T | edit tags |
| F2 | rename |
| Del | move to trash |
| Ctrl+Z | undo the last move, rename or delete |
| Enter | open in your video player |
| Ctrl+F | search |

Deleted clips go to `downloads/.reelgrab/trash`, and Ctrl+Z brings them back with their tags. Tags and favorites live in a small SQLite file in `downloads/.reelgrab/`, next to the thumbnails and the shrunk copies. If you rename or move clips in Explorer while the app is running, it notices within a couple of seconds and keeps their tags.

Settings (the gear): the hotkey, the size limit (raise it if you have Nitro), whether Enter in the picker pastes or only copies, the archive folder, launch at startup, and a button to recreate the shortcuts. They're saved in `%APPDATA%\reelgrab\settings.json`, and `REELGRAB_ARCHIVE_DIR` overrides the folder.

A few things can't be tested automatically, so check them by hand after changing the send code:

1. Ctrl+C a clip, Ctrl+V in Discord, and it uploads.
2. Dragging a tile into Discord uploads it, and dragging one to the desktop leaves the archive's file where it was.
3. Ctrl+Shift+M in Discord, type, Enter, and the clip is in the message box.
4. A clip over 10 MB arrives under 10 MB and plays in the Discord phone app.
5. With the main window closed, the hotkey still opens the picker.

## How the downloading works

### Instagram

1. The link becomes a media ID. A reel URL like `/reel/DAbc123XyZ_/` holds a shortcode, which is the post's numeric media ID (`pk`) written in URL-safe base64. `shortcode.py` decodes it. `/share/` links get resolved by following their redirect. Private-post shortcodes have 28 extra characters on the end, which get trimmed.
2. It looks like a browser. Instagram fingerprints the TLS handshake and HTTP/2 settings, and plain `requests` hits login walls a lot more often. The client uses `curl_cffi` to replay Chrome's fingerprint, then loads the homepage once for the `csrftoken` cookie (sent back as `X-CSRFToken`) and the `LSD` token that Relay GraphQL calls need. Every API call also sends `X-IG-App-ID: 936619743392459`, `X-ASBD-ID` and `X-IG-WWW-Claim: 0`.
3. It asks for the media, falling back through these until one works (`extractor.py`):

| # | Strategy | Request | Where the video is |
|---|----------|---------|--------------------|
| 0 | v1 API (only with a session cookie) | `GET /api/v1/media/{pk}/info/` | `items[0]` |
| 1 | Polaris GraphQL (yt-dlp's method) | `POST /api/graphql`, `doc_id=27130156389949648`, `variables={"media_id": pk}` | `data.xig_polaris_media.if_not_gated_logged_out` |
| 2 | web_info GraphQL (instaloader's method) | `POST /graphql/query`, `doc_id=27128499623469141`, `variables={"shortcode": ...}` | `data.xdt_api__v1__media__shortcode__web_info.items[0]` |
| 3 | Post page scrape | `GET /p/{code}/` | Relay prefetch JSON in `<script type="application/json" data-sjs>` |
| 4 | Embed page scrape | `GET /p/{code}/embed/captioned/` | `video_url` inside the escaped `contextJSON` blob |

   Before any of that it calls `/api/v1/web/get_ruling_for_content/`, Instagram's own "can a logged-out user see this" check, so a failure comes back with a reason you can read (age-restricted, for example).

4. It picks the best file. A post has progressive mp4s (`video_versions`, audio already included) and an inline MPEG-DASH manifest with separate video and audio tracks, which is often higher resolution. In `best` mode (the default) it compares the two. If the DASH video wins, it downloads that track plus the best audio and joins them with ffmpeg. At equal resolution it prefers an H.264 track, because a VP9 one would have to be re-encoded afterwards. Carousels with several videos give one file each (zipped in the web UI).
5. It downloads. The CDN links (`*.cdninstagram.com`, `*.fbcdn.net`) are signed and expire, so they're fetched right away with an Instagram `Referer`. Files are written as `.part.mp4` and only renamed once they're finished and converted, so the archive app never picks up half a download.

### TikTok

This follows what yt-dlp does as of 2026 (`tiktok.py`).

- Short links get followed to the real `/@user/video/<id>` URL. If one lands on the TikTok homepage (a dead link does that), you get an error saying the link doesn't point to a video.
- The video page embeds its data in `<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`, at `webapp.video-detail`, then `itemInfo.itemStruct`.
- `video.bitrateInfo` lists every encode TikTok has. It takes the most pixels first, then H.264 over H.265, then the higher bitrate. On yt-dlp's test video that means the 720p H.265 over the 540p H.264, converted afterwards. `downloadAddr` is the watermarked version and never gets used.
- The CDN returns 403 unless the request has `Referer: https://www.tiktok.com/` and the cookies the page set, so each video carries its own headers and reuses the page's session.
- Sometimes TikTok answers with a bot check instead of the page: a base64 challenge where you find the number whose SHA-256 matches. `solve_waf()` works it out, sets the cookie, and fetches the page again.

## Config

Copy `.env.example` to `.env` (compose picks it up on its own; the archive app reads it too).

| Var | What it's for |
|-----|---------|
| `IG_SESSIONID` | The `sessionid` cookie from a logged-in browser. Turns on strategy 0 and gets you past anonymous rate limits. Use a throwaway account. |
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

## Layout

```
reelgrab/
  shortcode.py   instagram url parsing, shortcode <-> media id
  client.py      curl_cffi session, csrf + lsd bootstrap, cookies
  extractor.py   the 5 instagram strategies, and the tiktok hand-off
  tiktok.py      tiktok links, page parsing, encode choice, bot check
  dash.py        MPD parsing, best video/audio pick
  downloader.py  quality plan, streaming download, ffmpeg mux + H.264 conversion
  cli.py         the `reelgrab` command
  web.py         FastAPI app (/api/info, /api/download, /api/thumb)
  static/        web ui
  archive/       the Windows meme app (PySide6)
    store.py       sqlite: tags, favorites, send history
    library.py     folders, moves, trash, undo
    media.py       thumbnails and shrinking under the size limit
    sending.py     picks the file Discord gets, puts it on the clipboard
    winapp.py      shortcuts and launch at startup
    ui/            main window, tray panel, hotkey picker
tests/           offline tests (mocked instagram, a saved tiktok page) + opt-in live tests
docs/            the design notes and plans the archive app was built from
```

The tests run offline. `REELGRAB_LIVE=1 pytest` also downloads one real TikTok (yt-dlp's long-standing public test video) to check the whole path still works.

Only download stuff you have the right to. Reposting other people's videos can break Instagram's and TikTok's terms and copyright.
