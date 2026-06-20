"""窗口焦点检测 — 项目级共享。

用法::

    from core.focus import FocusGuard

    guard = FocusGuard(on_pause=render_paused, on_resume=render_running)
    while guard.check_or_pause():
        do_work()
"""

import ctypes
import ctypes.wintypes
import time

from .keyboard import try_read_key
from udlrtui import K

# ── Win32 API ─────────────────────────────────────────────

_user32 = ctypes.windll.user32


def get_foreground_title() -> str:
    """获取当前前台窗口标题。"""
    hwnd = _user32.GetForegroundWindow()
    length = _user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


_GAME_KEYWORDS = [
    "forza horizon 6",
    "极限竞速：地平线 6",
    "极限竞速:地平线6",
    "极限竞速：地平线6",
    "极限竞速 地平线 6",
    "forzahorizon6",
]


def is_game_focused() -> bool:
    """当前前台窗口是否为地平线 6。"""
    title = get_foreground_title().lower()
    if not title:
        return False
    return any(kw in title for kw in _GAME_KEYWORDS)


# ── FocusGuard ────────────────────────────────────────────

class FocusGuard:
    """通用焦点守卫：游戏失焦时暂停，聚焦后恢复。

    Args:
        on_pause: 暂停时的回调 (window_title: str) -> None
        on_resume: 恢复时的回调 () -> None
        on_exit: 用户在暂停期间按 Esc 时的回调 () -> None
    """

    def __init__(self, on_pause=None, on_resume=None, on_exit=None):
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_exit = on_exit

    def check_or_pause(self) -> bool:
        """检查焦点，未聚焦则阻塞直到恢复。

        Returns:
            True  — 游戏已聚焦，可继续运行
            False — 用户在暂停期间按了 Esc，应退出
        """
        if is_game_focused():
            return True

        if self.on_pause:
            self.on_pause(get_foreground_title())

        while not is_game_focused():
            key = try_read_key()
            if key is not None and key in (K.ESC, K.BS):
                if self.on_exit:
                    self.on_exit()
                return False
            time.sleep(0.3)

        if self.on_resume:
            self.on_resume()
        time.sleep(0.3)
        return True
