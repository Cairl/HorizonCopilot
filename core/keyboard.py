"""通用键盘读取 — 兼容 msvcrt 和 ANSI 两种终端格式。

Windows 终端可能发送:
  - msvcrt 格式: \\xe0 + 扫描码 (经典 cmd.exe)
  - ANSI 格式:   \\x1b + [ + 字母 (Windows Terminal / VS Code / mintty)

本模块统一处理，返回 udlrtui K 常量。

Shift+方向键检测:
  - msvcrt 格式: 读取扫描码后通过 ``GetAsyncKeyState`` 检查 Shift 状态
  - ANSI 格式:   解析修饰符序列 ``\\x1b[1;2X`` (2 = Shift)
"""

import ctypes
import msvcrt
import time

from udlrtui import K

# Virtual-key code for Shift (used by GetAsyncKeyState)
_VK_SHIFT = 0x10


def _shift_held() -> bool:
    """Return ``True`` if either Shift key is currently held down."""
    return ctypes.windll.user32.GetAsyncKeyState(_VK_SHIFT) & 0x8000 != 0


# ANSI 第三字节 → K 常量映射
_ANSI_ARROW_MAP = {
    b"A": K.UP,
    b"B": K.DOWN,
    b"C": K.RIGHT,
    b"D": K.LEFT,
}

# ANSI Shift+方向键末字节 → K 常量
_ANSI_SHIFT_ARROW_MAP = {
    b"A": K.SHIFT_UP,
    b"B": K.SHIFT_DOWN,
    b"C": K.SHIFT_RIGHT,
    b"D": K.SHIFT_LEFT,
}

# msvcrt 方向键扫描码 → K Shift 常量
_MSVCRT_SHIFT_ARROW_MAP = {
    K.UP: K.SHIFT_UP,
    K.DOWN: K.SHIFT_DOWN,
    K.LEFT: K.SHIFT_LEFT,
    K.RIGHT: K.SHIFT_RIGHT,
}


def read_key() -> bytes:
    """阻塞读取一个按键，返回 K 常量格式的字节。

    自动处理:
    - msvcrt 扩展键前缀 (\\xe0 / \\x00)
    - ANSI 转义序列 (\\x1b[A 等)
    - Shift+方向键 (msvcrt 通过 GetAsyncKeyState，ANSI 通过 [1;2X 修饰符)
    - F2 等功能键 (\\x00 + ;)

    Returns:
        K.UP / K.DOWN / K.LEFT / K.RIGHT / K.ENTER / K.ESC / K.BS 等，
        以及 K.SHIFT_UP / K.SHIFT_DOWN / K.SHIFT_LEFT / K.SHIFT_RIGHT
    """
    while True:
        if not msvcrt.kbhit():
            time.sleep(0.01)
            continue
        raw = msvcrt.getch()

        # msvcrt 扩展键前缀
        if raw == b"\x00":
            ext = msvcrt.getch()
            if ext == b";":
                return b"\x00"  # F2 信号 (调用方处理)
            continue
        if raw == b"\xe0":
            scan = msvcrt.getch()
            # Shift+方向键检测 (msvcrt 格式)
            if scan in _MSVCRT_SHIFT_ARROW_MAP and _shift_held():
                return _MSVCRT_SHIFT_ARROW_MAP[scan]
            return scan

        # ANSI 转义序列
        if raw == b"\x1b":
            if msvcrt.kbhit():
                seq = msvcrt.getch()
                if seq == b"[" and msvcrt.kbhit():
                    code = msvcrt.getch()
                    # Shift+方向键: \x1b[1;2A/B/C/D
                    if code == b"1" and msvcrt.kbhit():
                        semi = msvcrt.getch()
                        if semi == b";" and msvcrt.kbhit():
                            mod = msvcrt.getch()
                            if mod == b"2" and msvcrt.kbhit():
                                final = msvcrt.getch()
                                return _ANSI_SHIFT_ARROW_MAP.get(final, K.ESC)
                    return _ANSI_ARROW_MAP.get(code, K.ESC)
                return K.ESC  # 不认识的序列
            return K.ESC  # 没有后续 → 真正的 Esc

        return raw


def try_read_key() -> bytes | None:
    """非阻塞读取。有按键返回 K 常量，无按键返回 None。"""
    if not msvcrt.kbhit():
        return None
    return read_key()
