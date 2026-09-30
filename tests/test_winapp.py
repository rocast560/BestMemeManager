import base64
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="windows integration")

TEST_VALUE = "reelgrab-test"


def _read_lnk(path: Path) -> dict:
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{path}'); "
              "Write-Output $s.TargetPath; Write-Output $s.Arguments; "
              "Write-Output $s.WorkingDirectory; Write-Output $s.IconLocation")
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    out = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
    return dict(zip(["target", "args", "cwd", "icon"], out))


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    from reelgrab.archive import winapp
    repo = tmp_path / "my repo é"
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(winapp, "REPO", repo)
    return repo


def test_launch_command_quotes_paths_with_spaces(fake_repo):
    from reelgrab.archive import winapp
    target, args = winapp.launch_command(tray=True)
    assert target == str(fake_repo / ".venv" / "Scripts" / "pythonw.exe")
    assert args == "-m reelgrab.archive.app --tray"
    assert winapp.launch_command(tray=False)[1] == "-m reelgrab.archive.app"
    assert winapp.run_value(tray=True) == f'"{target}" -m reelgrab.archive.app --tray'


def test_icon_exists_and_is_ico():
    from reelgrab.archive import winapp
    p = winapp.ensure_icon()
    assert p == winapp.ICON_PATH and p.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_create_shortcuts(tmp_path, fake_repo):
    from reelgrab.archive import winapp
    desk, start = tmp_path / "Desk top", tmp_path / "Start Menu"
    desk.mkdir(), start.mkdir()
    made = winapp.create_shortcuts(desk, start)
    assert made == [desk / "reelgrab archive.lnk", start / "reelgrab archive.lnk"]
    info = _read_lnk(made[0])
    assert info["target"] == str(fake_repo / ".venv" / "Scripts" / "pythonw.exe")
    assert info["args"] == "-m reelgrab.archive.app"
    assert info["cwd"] == str(fake_repo)
    assert info["icon"].startswith(str(winapp.ICON_PATH))


def test_startup_toggle(fake_repo):
    import winreg
    from reelgrab.archive import winapp
    try:
        winapp.set_startup(True, TEST_VALUE)
        assert winapp.is_startup_enabled(TEST_VALUE)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, winapp.RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, TEST_VALUE)
        assert value == winapp.run_value(tray=True) and value.startswith('"') and value.endswith("--tray")
        winapp.set_startup(False, TEST_VALUE)
        assert not winapp.is_startup_enabled(TEST_VALUE)
        winapp.set_startup(False, TEST_VALUE)  # already off: no error
    finally:
        winapp.set_startup(False, TEST_VALUE)
