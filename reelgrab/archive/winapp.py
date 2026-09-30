"""makes the archive feel installed on windows: an .ico, desktop/start-menu shortcuts and a
launch-at-login entry (HKCU Run key, no admin needed). everything points back at this repo's
.venv so code changes apply on the next launch.

    python -m reelgrab.archive.winapp --shortcuts     # create both shortcuts
"""

import base64
import os
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


def _powershell(script: str, env: dict[str, str] | None = None) -> str:
    """runs a fixed script. data goes in through RG_* environment variables, never spliced into
    the source, so no path (quotes, typographic quotes, $, backticks) can change what runs."""
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"  # default is the oem codepage
              "$ErrorActionPreference = 'Stop'\n"
              "try {\n" + script + "\n} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n")
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=_NO_WINDOW, env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout).strip() or f"powershell exited {r.returncode}")
    return r.stdout


_SHORTCUT_PS = """
$sh = New-Object -ComObject WScript.Shell
$dirs = @($env:RG_DIR0, $env:RG_DIR1)
if (-not $dirs[0]) { $dirs[0] = [Environment]::GetFolderPath('Desktop') }
if (-not $dirs[1]) { $dirs[1] = [Environment]::GetFolderPath('Programs') }
foreach ($d in $dirs) {
  if (-not (Test-Path -LiteralPath $d -PathType Container)) { throw "folder not found: $d" }
  $p = Join-Path $d ($env:RG_NAME + '.lnk')
  $s = $sh.CreateShortcut($p)
  $s.TargetPath = $env:RG_TARGET
  $s.Arguments = $env:RG_ARGS
  $s.WorkingDirectory = $env:RG_CWD
  $s.IconLocation = $env:RG_ICON + ',0'
  $s.Description = 'Store memes and send them to Discord fast'
  $s.Save()
  Write-Output $p
}
"""


def create_shortcuts(desktop: Path | None = None, start_menu: Path | None = None) -> list[Path]:
    if not IS_WIN:
        return []
    icon = ensure_icon()
    target, args = launch_command(tray=False)
    env = {"RG_TARGET": target, "RG_ARGS": args, "RG_CWD": str(REPO), "RG_ICON": str(icon), "RG_NAME": APP_NAME,
           "RG_DIR0": str(desktop) if desktop else "", "RG_DIR1": str(start_menu) if start_menu else ""}
    made = [Path(line.strip()) for line in _powershell(_SHORTCUT_PS, env).splitlines() if line.strip()]
    missing = [p for p in made if not p.exists()]
    if missing or len(made) != 2:
        raise OSError(f"shortcut wasn't written: {missing or made}")
    return made


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
