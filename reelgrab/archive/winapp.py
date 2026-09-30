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
APP_NAME = "BestMemeManager"
APP_ID = "reelgrab.archive"  # the process sets this too; the taskbar matches the two up
LEGACY_NAMES = ("reelgrab archive",)  # what shortcuts and the startup entry used to be called
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


# WScript.Shell can't set System.AppUserModel.ID on a shortcut, so the shell link api is
# compiled inline (Windows PowerShell ships the C# compiler). without that id the taskbar
# can fall back to pythonw's icon and calls the app "Python".
_LINK_CS = r'''
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

public static class RgLink {
  [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int c, IntPtr fd, uint fl);
    void GetIDList(out IntPtr p);
    void SetIDList(IntPtr p);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string s);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string s);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string s);
    void GetHotkey(out short h);
    void SetHotkey(short h);
    void GetShowCmd(out int c);
    void SetShowCmd(int c);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c, out int i);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string s, int i);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string s, uint r);
    void Resolve(IntPtr hwnd, uint f);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string s);
  }

  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    void GetCount(out uint c);
    void GetAt(uint i, out PropertyKey k);
    void GetValue(ref PropertyKey k, out PropVariant v);
    void SetValue(ref PropertyKey k, ref PropVariant v);
    void Commit();
  }

  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  struct PropertyKey { public Guid fmtid; public uint pid; }

  [StructLayout(LayoutKind.Sequential)]
  struct PropVariant { public ushort vt; public ushort r1, r2, r3; public IntPtr p; public IntPtr p2; }

  [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
  class ShellLink {}

  public static void Save(string path, string target, string args, string cwd, string icon, string desc, string appId) {
    var link = (IShellLinkW)new ShellLink();
    link.SetPath(target);
    link.SetArguments(args);
    link.SetWorkingDirectory(cwd);
    link.SetIconLocation(icon, 0);
    link.SetDescription(desc);
    // System.AppUserModel.ID: ties running windows with this app id to this shortcut
    var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    var pv = new PropVariant { vt = 31 /* VT_LPWSTR */, p = Marshal.StringToCoTaskMemUni(appId) };
    var store = (IPropertyStore)link;
    try { store.SetValue(ref key, ref pv); store.Commit(); }
    finally { Marshal.FreeCoTaskMem(pv.p); }
    ((IPersistFile)link).Save(path, true);
  }
}
'''

_SHORTCUT_PS = """
Add-Type -TypeDefinition $env:RG_CS -Language CSharp
$dirs = @($env:RG_DIR0, $env:RG_DIR1)
if (-not $dirs[0]) { $dirs[0] = [Environment]::GetFolderPath('Desktop') }
if (-not $dirs[1]) { $dirs[1] = [Environment]::GetFolderPath('Programs') }
foreach ($d in $dirs) {
  if (-not (Test-Path -LiteralPath $d -PathType Container)) { throw "folder not found: $d" }
  foreach ($old in $env:RG_LEGACY.Split('|')) {
    if ($old) {
      $op = Join-Path $d ($old + '.lnk')
      if (Test-Path -LiteralPath $op) { Remove-Item -LiteralPath $op -Force }
    }
  }
  $p = Join-Path $d ($env:RG_NAME + '.lnk')
  [RgLink]::Save($p, $env:RG_TARGET, $env:RG_ARGS, $env:RG_CWD, $env:RG_ICON, 'Store memes and send them to Discord fast', $env:RG_APPID)
  Write-Output $p
}
"""


def create_shortcuts(desktop: Path | None = None, start_menu: Path | None = None) -> list[Path]:
    if not IS_WIN:
        return []
    icon = ensure_icon()
    target, args = launch_command(tray=False)
    env = {"RG_TARGET": target, "RG_ARGS": args, "RG_CWD": str(REPO), "RG_ICON": str(icon), "RG_NAME": APP_NAME,
           "RG_APPID": APP_ID, "RG_LEGACY": "|".join(LEGACY_NAMES), "RG_CS": _LINK_CS,
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
        stale = [] if enabled else [value_name]
        if value_name == APP_NAME:
            stale += list(LEGACY_NAMES)  # entries left under the old name
        for name in stale:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


def is_startup_enabled(value_name: str = APP_NAME) -> bool:
    if not IS_WIN:
        return False
    import winreg
    names = [value_name] + (list(LEGACY_NAMES) if value_name == APP_NAME else [])
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            for name in names:
                try:
                    winreg.QueryValueEx(k, name)
                    return True
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass
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
