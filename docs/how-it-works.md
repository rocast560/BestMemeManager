# How it works

## Why the files play on phones

Instagram serves its highest-quality video as VP9 inside an .mp4 file. Desktop Chrome and desktop Discord play that fine. iOS doesn't, so Discord on an iPhone shows a dead video. Every download gets checked with ffprobe, and anything that isn't H.264 video with AAC audio is re-encoded (CRF 18, so you won't see the difference). TikTok often sends H.265, which gets the same treatment.

## Instagram

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

## TikTok

This follows what yt-dlp does as of 2026 (`tiktok.py`).

- Short links get followed to the real `/@user/video/<id>` URL, and only to TikTok hosts. If one lands on the TikTok homepage (a dead link does that), you get an error saying the link doesn't point to a video.
- The video page embeds its data in `<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`, at `webapp.video-detail`, then `itemInfo.itemStruct`.
- `video.bitrateInfo` lists every encode TikTok has. It takes the most pixels first, then H.264 over H.265, then the higher bitrate. On yt-dlp's test video that means the 720p H.265 over the 540p H.264, converted afterwards. Without ffmpeg it goes H.264 first, since an H.265 file would stay unplayable on phones. `downloadAddr` is the watermarked version and never gets used.
- The CDN returns 403 unless the request has `Referer: https://www.tiktok.com/` and the cookies the page set, so each video carries its own headers and reuses the page's session.
- Sometimes TikTok answers with a bot check instead of the page: a base64 challenge where you find the number whose SHA-256 matches. `solve_waf()` works it out, sets the cookie, and fetches the page again.

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
docs/            these notes, plus the design specs and plans the app was built from
```

The tests run offline. `REELGRAB_LIVE=1 pytest` also downloads one real TikTok (yt-dlp's long-standing public test video) to check the whole path still works.
