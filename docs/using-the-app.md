# Using the meme archive app

## Install

You need Python 3.10+ and ffmpeg (`winget install Gyan.FFmpeg`). Double-click `start-archive.bat`. The first run makes a `.venv`, installs PySide6, and puts a "BestMemeManager" shortcut on your Desktop and in the Start menu. Use the shortcut after that.

To have it start with Windows, open settings (the gear) and tick "Launch at Windows startup". It starts hidden in the tray at login.

The app uses the `downloads/` folder, the same one the Docker downloader saves into, so anything you grab from the web page shows up in the app's Inbox.

## The main window

There's a folder tree on the left (Favorites, Recent and Inbox are pinned at the top) and a grid of thumbnails that play when you hover over them. Paste a link in the top box and the clip downloads into Inbox. You can also drag mp4s in from Explorer. Drag clips onto a folder to move them, and right-click the tree to make, rename or delete folders.

## Getting a clip into Discord

- Select it and press Ctrl+C, then Ctrl+V in Discord. The app puts the file itself on the clipboard, same as copying it in Explorer.
- Drag the tile straight into Discord. Dropping it on your desktop makes a copy, and the archive keeps its file.
- Press Ctrl+Shift+M from anywhere. A search box pops up, you type a few letters, press Enter, and the clip gets pasted into whatever window you were in. Shift+Enter copies without pasting.
- Left-click the tray icon (by the clock and wifi) for a smaller panel with search, Favorites / Recent / Folder tabs, and a link box. Click a clip to copy it. Right-click it to favorite, tag, move or delete it. Double-clicking the tray icon opens the full window.

Closing the main window doesn't quit the app, so the hotkey and tray panel keep working. Quit from the tray menu.

Every clip gets converted to H.264 video with AAC audio so it plays on phones (Instagram's VP9 and TikTok's H.265 don't play on iPhones). Discord caps free uploads at 10 MB, so anything bigger gets a shrunk copy, and that copy is what Discord receives. Your original stays as it was, and the copy keeps the clip's name.

## Keys

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

## Where things are kept

Deleted clips go to `downloads/.reelgrab/trash`, and Ctrl+Z brings them back with their tags. Tags and favorites live in a small SQLite file in `downloads/.reelgrab/`, next to the thumbnails and the shrunk copies. If you rename or move clips in Explorer while the app is running, it notices within a couple of seconds and keeps their tags.

Settings (the gear): the hotkey, the size limit (raise it if you have Nitro), whether Enter in the picker pastes or only copies, the archive folder, launch at startup, and a button to recreate the shortcuts. They're saved in `%APPDATA%\reelgrab\settings.json`, and `REELGRAB_ARCHIVE_DIR` overrides the folder.

## Checking it by hand

A few things can't be tested automatically, so check them after changing the send code:

1. Ctrl+C a clip, Ctrl+V in Discord, and it uploads.
2. Dragging a tile into Discord uploads it, and dragging one to the desktop leaves the archive's file where it was.
3. Ctrl+Shift+M in Discord, type, Enter, and the clip is in the message box.
4. A clip over 10 MB arrives under 10 MB and plays in the Discord phone app.
5. With the main window closed, the hotkey still opens the picker.
