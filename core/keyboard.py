"""通用键盘读取 — 兼容 msvcrt 和 ANSI 两种终端格式。

Windows 终端可能发送:
  - msvcrt 格式: \\xe0 + 扫描码 (经典 cmd.exe)
  - ANSI 格式:   \\x1b + [ + 字母 (Windows Terminal / VS Code / mintty)

本模块统一处理，返回 udlrtui K 常量。
"""

import msvcrt
import time

from udlrtui import K

# ANSI 第三字节 → K 常量映射
_ANSI_ARROW_MAP = {
    b"A": K.UP,
    b"B": K.DOWN,
    b"C": K.RIGHT,
    b"D": K.LEFT,
}


def read_key() -> bytes:
    """阻塞读取一个按键，返回 K 常量格式的字节。

    自动处理:
    - msvcrt 扩展键前缀 (\\xe0 / \\x00)
    - ANSI 转义序列 (\\x1b[A 等)
    - F2 等功能键 (\\x00 + ;)

    Returns:
        K.UP / K.DOWN / K.LEFT / K.RIGHT / K.ENTER / K.ESC / K.BS 等
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
            return msvcrt.getch()  # 扫描码，与 K 常量一致

        # ANSI 转义序列
        if raw == b"\x1b":
            if msvcrt.kbhit():
                seq = msvcrt.getch()
                if seq == b"[" and msvcrt.kbhit():
                    code = msvcrt.getch()
                    return _ANSI_ARROW_MAP.get(code, K.ESC)
                return K.ESC  # 不认识的序列
            return K.ESC  # 没有后续 → 真正的 Esc

        return raw


def try_read_key() -> bytes | None:
    """非阻塞读取。有按键返回 K 常量，无按键返回 None。"""
    if not msvcrt.kbhit():
        return None
    return read_key()
