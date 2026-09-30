"""turns "Ctrl+Shift+M" into the (modifiers, virtual key) pair RegisterHotKey wants."""

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "alt": MOD_ALT,
         "win": MOD_WIN, "meta": MOD_WIN}
_NAMED = {"space": 0x20, "tab": 0x09}


def _vk(key: str) -> int:
    k = key.lower()
    if len(k) == 1 and (k.isalpha() or k.isdigit()) and k.isascii():
        return ord(k.upper())
    if k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x6F + int(k[1:])
    if k in _NAMED:
        return _NAMED[k]
    raise ValueError(f"unsupported key: {key!r}")


def parse_hotkey(s: str) -> tuple[int, int]:
    parts = [p.strip() for p in s.split("+")]
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"hotkey needs a modifier and a key: {s!r}")
    mods = MOD_NOREPEAT
    for p in parts[:-1]:
        if p.lower() not in _MODS:
            raise ValueError(f"unknown modifier: {p!r}")
        mods |= _MODS[p.lower()]
    return mods, _vk(parts[-1])
