"""HorizonCopilot — 地平线 6 辅助工具.

Usage::

    python main.py
"""

from __future__ import annotations

import atexit
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

from udlrtui import C, K, Renderer, Navigator, widgets as W
from udlrtui import init_console, get_key

from tasks import discover_tasks


# ── 单实例锁 ──────────────────────────────────────────────

_PID_FILE: Path = Path(tempfile.get_tempdir()) / "HorizonCopilot.pid"


def _process_exists(pid: int) -> bool:
    """检查指定 PID 的进程是否仍在运行（Windows）。"""
    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _kill_process(pid: int) -> None:
    """终止指定 PID 的进程（Windows ``TerminateProcess``）。"""
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


def acquire_single_instance() -> None:
    """确保只有一个实例在运行。

    如果 PID 文件存在且对应的进程仍在运行，则杀掉旧实例并等待其退出，
    然后写入当前进程的 PID。退出时通过 :mod:`atexit` 自动清理 PID 文件。
    """
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            old_pid = None

        if old_pid is not None and old_pid != os.getpid():
            if _process_exists(old_pid):
                _kill_process(old_pid)
                # 等待旧进程退出（最多 2 秒）
                for _ in range(20):
                    if not _process_exists(old_pid):
                        break
                    time.sleep(0.1)

    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_release_single_instance)


def _release_single_instance() -> None:
    """释放单实例锁（删除 PID 文件）。"""
    try:
        if _PID_FILE.exists():
            _PID_FILE.unlink()
    except OSError:
        pass


# ── 菜单 ──────────────────────────────────────────────────

def render_menu(nav: Navigator, renderer: Renderer, tasks: list[dict]) -> None:
    """Render the main task selection menu.

    Args:
        nav: Navigator for the task list.
        renderer: UdlrTui ``Renderer``.
        tasks: Discovered task info dicts.
    """
    w_val: int = 32
    lines: list[str] = [W.top_border("HorizonCopilot", w_val)]
    for i, item in enumerate(tasks):
        label = f"{C.WHITE}{item['label']}{C.RESET}"
        lines.append(
            W.line_sel(label, w_val) if nav.index == i else W.line(label, w_val)
        )
    lines.append(W.bottom_border(w_val))
    renderer.render(lines)


def main() -> None:
    """Entry point — discover tasks and show menu."""
    acquire_single_instance()
    init_console()
    renderer = Renderer()
    tasks: list[dict] = discover_tasks()
    nav: Navigator = Navigator(n_items=len(tasks))

    while True:
        render_menu(nav, renderer, tasks)
        key = get_key()
        if key in (K.ESC, K.BS):
            break
        if nav.handle(key):
            continue
        if key == K.ENTER:
            task_info: dict = tasks[nav.index]
            task_cls = task_info["task_class"]
            task = task_cls(renderer)
            task.run()


if __name__ == "__main__":
    main()
