import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    # the archive saves settings to %APPDATA%\reelgrab\settings.json; never let tests write the real one
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
