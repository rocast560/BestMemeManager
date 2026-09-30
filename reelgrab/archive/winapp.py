"""makes the archive feel installed on windows: an .ico, desktop/start-menu shortcuts and a
launch-at-login entry (HKCU Run key, no admin needed). everything points back at this repo's
.venv so code changes apply on the next launch.

    python -m reelgrab.archive.winapp --shortcuts     # create both shortcuts
"""

import base64
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ICON_PATH = Path(__file__).resolve().parent / "assets" / "reelgrab.ico"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "reelgrab archive"
IS_WIN = sys.platform == "win32"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def ensure_icon() -> Path:
    if not ICON_PATH.exists():
        from PySide6.QtGui import QGuiApplication
        from .ui.icons import app_icon
        _app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - pixmaps need an app
        ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not app_icon().pixmap(256, 256).save(str(ICON_PATH), "ICO"):
            raise OSError(f"couldn't write {ICON_PATH}")
    return ICON_PATH


def _pythonw() -> Path:
    venv = REPO / ".venv" / "Scripts" / "pythonw.exe"
    if venv.exists():
        return venv
    here = Path(sys.executable)
    return here.with_name("pythonw.exe") if here.with_name("pythonw.exe").exists() else here


def launch_command(tray: bool) -> tuple[str, str]:
    return str(_pythonw()), "-m reelgrab.archive.app" + (" --tray" if tray else "")


def run_value(tray: bool = True) -> str:
    target, args = launch_command(tray)
    return f'"{target}" {args}'


def _powershell(script: str) -> str:
    # -EncodedCommand sidesteps every quoting problem with spaces/unicode in paths
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script  # default is the oem codepage
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout).strip() or f"powershell exited {r.returncode}")
    return r.stdout


def _ps_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def create_shortcuts(desktop: Path | None = None, start_menu: Path | None = None) -> list[Path]:
    if not IS_WIN:
        return []
    icon = ensure_icon()
    target, args = launch_command(tray=False)
    dirs = [
        _ps_str(str(desktop)) if desktop else "[Environment]::GetFolderPath('Desktop')",
        _ps_str(str(start_menu)) if start_menu else "[Environment]::GetFolderPath('Programs')",
    ]
    script = "$sh = New-Object -ComObject WScript.Shell\n"
    for d in dirs:
        script += (f"$p = Join-Path ({d}) {_ps_str(APP_NAME + '.lnk')}\n"
                   "$s = $sh.CreateShortcut($p)\n"
                   f"$s.TargetPath = {_ps_str(target)}\n"
                   f"$s.Arguments = {_ps_str(args)}\n"
                   f"$s.WorkingDirectory = {_ps_str(str(REPO))}\n"
                   f"$s.IconLocation = {_ps_str(str(icon) + ',0')}\n"
                   "$s.Description = 'Store memes and send them to Discord fast'\n"
                   "$s.Save()\nWrite-Output $p\n")
    return [Path(line.strip()) for line in _powershell(script).splitlines() if line.strip()]


def set_startup(enabled: bool, value_name: str = APP_NAME) -> None:
    if not IS_WIN:
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enabled:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, run_value(tray=True))
        else:
            try:
                winreg.DeleteValue(k, value_name)
            except FileNotFoundError:
                pass


def is_startup_enabled(value_name: str = APP_NAME) -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, value_name)
            return True
    except FileNotFoundError:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--shortcuts" in argv:
        for p in create_shortcuts():
            print(f"created {p}")
    if "--startup" in argv:
        set_startup(True)
        print("launch at login: on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
