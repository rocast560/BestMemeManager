"""the few win32 calls qt doesn't cover: global hotkey, focus juggling and a synthetic ctrl+v.
everything is a harmless no-op on other platforms."""

import sys

WM_HOTKEY = 0x0312
IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]

    INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 2
    VK_CONTROL, VK_SHIFT, VK_MENU, VK_V = 0x11, 0x10, 0x12, 0x56

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        # MOUSEINPUT is the largest member; pad so sizeof(INPUT) matches what SendInput expects
        _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def register_hotkey(hwnd: int, hk_id: int, mods: int, vk: int) -> bool:
    return bool(IS_WIN and user32.RegisterHotKey(hwnd, hk_id, mods, vk))


def unregister_hotkey(hwnd: int, hk_id: int) -> None:
    if IS_WIN:
        user32.UnregisterHotKey(hwnd, hk_id)


def foreground_window() -> int:
    return (user32.GetForegroundWindow() or 0) if IS_WIN else 0


def focus_window(hwnd: int) -> bool:
    return bool(IS_WIN and hwnd and user32.IsWindow(hwnd) and user32.SetForegroundWindow(hwnd))


def send_ctrl_v() -> None:
    if not IS_WIN:
        return
    def key(vk, up=False):
        i = INPUT(type=INPUT_KEYBOARD)
        i.u.ki = KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP if up else 0)
        return i
    # the hotkey's shift/alt may still be held; release them so the target sees plain ctrl+v
    seq = [key(VK_SHIFT, True), key(VK_MENU, True),
           key(VK_CONTROL), key(VK_V), key(VK_V, True), key(VK_CONTROL, True)]
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
