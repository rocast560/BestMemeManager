(made by me)

# BestMemeManager

Paste an Instagram reel or TikTok link, get an mp4 that plays on phones too, and get it into Discord in two keystrokes.

I made this because the mp4s I posted in Discord wouldn't play for anyone on mobile. It's a downloader (web page + CLI, runs in Docker) plus a Windows app for keeping memes in folders. Ctrl+C a clip and paste it in Discord, drag it in, or hit Ctrl+Shift+M from anywhere and type its name.

## Run it

Windows app: install Python 3.10+ and ffmpeg, then double-click `start-archive.bat`. It adds a Desktop shortcut for next time.

Downloader: `docker compose up -d --build`, then open http://localhost:8080.

## More

- [Using the app](docs/using-the-app.md): hotkeys, tray panel, folders, settings
- [Downloader setup and fixes](docs/downloader.md): CLI, config, what to do when Instagram changes things
- [How it works](docs/how-it-works.md): the Instagram and TikTok details, and why the files play on phones

Only download stuff you have the right to.
