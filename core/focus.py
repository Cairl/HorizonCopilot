"""窗口焦点检测 — 项目级共享。

用法::

    from core.focus import FocusGuard, activate_game_window

    activate_game_window()  # 启动时切到游戏窗口
    guard = FocusGuard(on_exit=on_exit_callback)
    while guard.check():
        do_work()
    # 失焦后自动退出循环
"""

import ctypes
import ctypes.wintypes

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


# ── 游戏窗口查找与激活 ────────────────────────────────────

# Win32 枚举窗口所需类型
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM,
)


def _match_game_title(title: str) -> bool:
    """判断窗口标题是否匹配地平线 6。"""
    if not title:
        return False
    lower = title.lower()
    return any(kw in lower for kw in _GAME_KEYWORDS)


def find_game_window() -> int:
    """枚举顶层窗口，返回地平线 6 的窗口句柄（找不到返回 0）。"""
    found_hwnd = ctypes.wintypes.HWND(0)

    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found_hwnd
        length = _user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True  # 继续枚举
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        if _match_game_title(buf.value):
            found_hwnd = hwnd
            return False  # 停止枚举
        return True

    _user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return found_hwnd


def activate_game_window() -> bool:
    """激活地平线 6 窗口到前台。

    Windows 对 ``SetForegroundWindow`` 有前台锁定限制（仅当调用进程
    已在前台或被前台进程启动时才允许）。这里通过模拟一次 Alt 键释放
    前台锁，再调用 ``SetForegroundWindow``。

    切换窗口后自动检测并确保 Caps Lock 已开启。

    Returns:
        True  — 找到游戏窗口并尝试激活
        False — 未找到游戏窗口
    """
    hwnd = find_game_window()
    if not hwnd:
        return False

    # Alt 键 hack：按下并释放 Alt，绕过前台锁定限制
    VK_MENU = 0x12  # Alt
    _user32.keybd_event(VK_MENU, 0, 0, 0)        # key down
    _user32.keybd_event(VK_MENU, 0, 0x0002, 0)   # key up (KEYEVENTF_KEYUP)

    # 若窗口最小化，先恢复
    SW_RESTORE = 9
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)

    _user32.SetForegroundWindow(hwnd)

    # 确保 Caps Lock 已开启（游戏输入需要）
    VK_CAPITAL = 0x14
    if not (_user32.GetKeyState(VK_CAPITAL) & 1):
        _user32.keybd_event(VK_CAPITAL, 0, 0, 0)        # key down
        _user32.keybd_event(VK_CAPITAL, 0, 0x0002, 0)   # key up

    return True


# ── FocusGuard ────────────────────────────────────────────

class FocusGuard:
    """焦点守卫：游戏失焦时立即终止运行。

    失焦即返回 False，不再阻塞等待恢复。调用方应在每次按键前
    调用 :meth:`check`，返回 False 时终止运行流程。

    Args:
        on_exit: 失焦时的回调 () -> None
    """

    def __init__(self, on_exit=None):
        self.on_exit = on_exit

    def check(self) -> bool:
        """检查游戏是否聚焦。

        Returns:
            True  — 游戏已聚焦，可继续运行
            False — 游戏失焦，应立即终止运行（调用 on_exit 回调）
        """
        if is_game_focused():
            return True
        if self.on_exit:
            self.on_exit()
        return False
