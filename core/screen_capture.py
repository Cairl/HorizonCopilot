"""屏幕区域选择与截图捕获 — 项目级通用工具。

提供全屏覆盖拖拽框选和区域截图保存能力，供特征截取工作流使用。

用法::

    from core.screen_capture import select_region, capture_template_to

    region = select_region()
    if region is not None:
        capture_template_to(region, Path("template.png"))
"""

from __future__ import annotations

from pathlib import Path

import pyautogui


def select_region() -> tuple[int, int, int, int] | None:
    """全屏覆盖，用户拖拽框选区域。

    弹出全屏 tkinter 窗口，背景为当前屏幕截图，用户拖拽选择矩形区域。
    按 Esc 或点击外部取消。

    Returns:
        ``(x, y, w, h)`` 元组，或 ``None``（用户取消）。
    """
    import tkinter as tk

    try:
        import ctypes as _ct
        _ct.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    root = tk.Tk()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.destroy()

    screenshot = pyautogui.screenshot()
    result: dict = {}

    win = tk.Tk()
    win.attributes("-fullscreen", True)
    win.attributes("-topmost", True)
    win.overrideredirect(True)
    win.geometry(f"{sw}x{sh}+0+0")

    from PIL import ImageTk
    bg_img = ImageTk.PhotoImage(screenshot)
    canvas = tk.Canvas(win, width=sw, height=sh, highlightthickness=0,
                       cursor="cross")
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, anchor="nw", image=bg_img)

    state = {"sx": 0, "sy": 0, "rect": None}

    def on_press(e):
        state["sx"], state["sy"] = e.x, e.y
        if state["rect"]:
            canvas.delete(state["rect"])
            state["rect"] = None

    def on_drag(e):
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            state["sx"], state["sy"], e.x, e.y,
            outline="#89b4fa", width=3, dash=(6, 4))

    def on_release(e):
        x1, y1 = min(state["sx"], e.x), min(state["sy"], e.y)
        x2, y2 = max(state["sx"], e.x), max(state["sy"], e.y)
        w, h = x2 - x1, y2 - y1
        if w >= 10 and h >= 10:
            result["region"] = (x1, y1, w, h)
        win.destroy()

    def on_escape(e):
        win.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    win.bind("<Escape>", on_escape)
    win.focus_force()
    win.mainloop()
    return result.get("region")


def capture_template_to(
    region: tuple[int, int, int, int],
    filepath: Path,
) -> None:
    """对指定区域截图并保存为 PNG。

    Args:
        region: ``(x, y, w, h)`` 屏幕区域元组。
        filepath: 目标 PNG 文件路径（父目录自动创建）。
    """
    x, y, w, h = region
    img = pyautogui.screenshot(region=(x, y, w, h))
    filepath.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(filepath))
