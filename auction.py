"""拍卖行抢车 — 自动检测车辆并购买。

工作流程:
    1. 用户框选拍卖场"有车"区域 + 截取车辆图片特征
    2. 循环: Enter → Enter → 截图比对
       - 匹配到车辆: Y → ↓ → Enter → Enter (购买)
       - 未匹配: Esc → 重新循环
"""

import ctypes
import ctypes.wintypes
import json
import msvcrt
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyautogui
from PIL import Image

from udlrtui import C, K, Renderer, widgets as W
from udlrtui import get_key, drain_keyboard

# pyautogui 安全设置
pyautogui.FAILSAFE = True  # 鼠标移到左上角触发 FailSafe
pyautogui.PAUSE = 0

# ── 路径 ──────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"
TEMPLATE_FILE = DATA_DIR / "template.png"


# ── 配置 ──────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"region": None, "threshold": 0.90}


def _save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── 区域选择器 (tkinter overlay) ─────────────────────────

def _select_region() -> tuple[int, int, int, int] | None:
    """全屏覆盖，用户拖拽框选区域。返回 (x, y, w, h)。"""
    import tkinter as tk

    # Windows DPI 感知
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    root = tk.Tk()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.destroy()

    # 截屏作为背景
    screenshot = pyautogui.screenshot()

    result = {}

    win = tk.Toplevel() if False else tk.Tk()
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
        x1 = min(state["sx"], e.x)
        y1 = min(state["sy"], e.y)
        x2 = max(state["sx"], e.x)
        y2 = max(state["sy"], e.y)
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


def _capture_template(region: tuple[int, int, int, int]) -> None:
    """截取区域内的图片作为车辆特征模板。"""
    x, y, w, h = region
    img = pyautogui.screenshot(region=(x, y, w, h))
    DATA_DIR.mkdir(exist_ok=True)
    img.save(str(TEMPLATE_FILE))


# ── 图像匹配 ──────────────────────────────────────────────

def _match_template(screenshot: np.ndarray, template: np.ndarray) -> float:
    """在截图中查找模板，返回最大匹配置信度 [0, 1]。

    使用 OpenCV 模板匹配 + NCC (归一化互相关)。
    """
    if len(screenshot.shape) == 3:
        scr_gray = cv2.cvtColor(screenshot, cv2.COLOR_RGB2GRAY)
    else:
        scr_gray = screenshot
    if len(template.shape) == 3:
        tpl_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    else:
        tpl_gray = template

    th, tw = tpl_gray.shape[:2]
    sh, sw = scr_gray.shape[:2]
    if th > sh or tw > sw:
        return 0.0

    result = cv2.matchTemplate(scr_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return float(max_val)


# ── 按键 ──────────────────────────────────────────────────

def _press(key: str, interval: float = 0.05) -> None:
    """发送按键。支持: enter, esc, y, down, f2, f3。"""
    pyautogui.press(key)
    time.sleep(interval)


# ── 窗口焦点检测 ──────────────────────────────────────────

_user32 = ctypes.windll.user32

def _get_foreground_title() -> str:
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


def _is_game_focused() -> bool:
    """当前前台窗口是否为地平线 6。"""
    title = _get_foreground_title().lower()
    if not title:
        return False
    return any(kw in title for kw in _GAME_KEYWORDS)


# ── 主入口 ────────────────────────────────────────────────

def run_auction_sniper(renderer: Renderer) -> None:
    cfg = _load_config()
    region = cfg.get("region")
    threshold = cfg.get("threshold", 0.90)
    template = None

    if region and TEMPLATE_FILE.exists():
        template = cv2.imread(str(TEMPLATE_FILE))
        if template is not None:
            template = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)

    renderer.reset()
    drain_keyboard()

    running = True
    stats = {"attempts": 0, "found": 0}

    def status_line(text: str, color: str = C.WHITE) -> str:
        return f"{color}{text}{C.RESET}"

    def cfg_line() -> str:
        if region and template is not None:
            return f"{C.GREEN}已配置{C.RESET} ({region[2]}x{region[3]})"
        return f"{C.RED}未配置{C.RESET}"

    while running:
        # ── 渲染状态 ──
        has_tpl = template is not None
        W_VAL = 46
        lines = [
            W.top_border("拍卖行抢车", W_VAL),
            W.line(f"{C.LABEL}车辆特征:{C.RESET} {cfg_line()}", W_VAL),
            W.line(f"{C.LABEL}匹配置信度:{C.RESET} {C.WHITE}≥ {threshold:.0%}{C.RESET}", W_VAL),
            W.divider("", W_VAL),
            W.line(f"{C.LABEL}尝试次数:{C.RESET} {C.WHITE}{stats['attempts']}{C.RESET}", W_VAL),
            W.line(f"{C.LABEL}发现车辆:{C.RESET} {C.GREEN}{stats['found']}{C.RESET}", W_VAL),
            W.divider("", W_VAL),
        ]
        if has_tpl:
            lines.append(W.line(f"{C.GREEN}Enter{C.RESET}{C.GRAY} 开始  {C.GREEN}F2{C.RESET}{C.GRAY} 重新配置{C.RESET}", W_VAL))
        else:
            lines.append(W.line(f"{C.YELLOW}Enter{C.RESET}{C.GRAY} 配置车辆特征{C.RESET}", W_VAL))
        lines.append(W.line(f"{C.GRAY}Esc 返回主菜单{C.RESET}", W_VAL))
        lines.append(W.bottom_border(W_VAL))
        renderer.render(lines)

        # ── 等待按键 ──
        drain_keyboard()
        while True:
            if msvcrt.kbhit():
                raw = msvcrt.getch()
                if raw in (b"\xe0", b"\x00"):
                    msvcrt.getch()
                    continue
                key = raw
                break
            time.sleep(0.016)
        else:
            continue

        if key == K.ESC:
            renderer.reset()
            return

        # ── F2: 重新配置 (msvcrt: \x00 + ;) ──
        if key == b"\x00":
            ext = msvcrt.getch()
            if ext == b";":  # F2 扫描码 0x3B
                _do_configure(renderer, cfg)
                cfg = _load_config()
                region = cfg.get("region")
                if region and TEMPLATE_FILE.exists():
                    template = cv2.imread(str(TEMPLATE_FILE))
                    if template is not None:
                        template = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
                renderer.reset()
                continue

        # ── Enter: 开始/配置 ──
        if key == K.ENTER:
            if not has_tpl:
                _do_configure(renderer, cfg)
                cfg = _load_config()
                region = cfg.get("region")
                if region and TEMPLATE_FILE.exists():
                    template = cv2.imread(str(TEMPLATE_FILE))
                    if template is not None:
                        template = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
                renderer.reset()
            else:
                _run_snipe_loop(renderer, cfg, template, region, threshold, stats)


def _do_configure(renderer: Renderer, cfg: dict) -> None:
    """配置流程: 框选区域 → 截取模板。"""
    W_VAL = 46
    renderer.reset()

    # 步骤 1: 框选
    lines = [
        W.top_border("配置车辆特征", W_VAL),
        W.line(f"{C.YELLOW}请在屏幕上拖拽框选有车时的特征区域{C.RESET}", W_VAL),
        W.line(f"{C.GRAY}框选完毕自动截取，Esc 取消{C.RESET}", W_VAL),
        W.bottom_border(W_VAL),
    ]
    renderer.render(lines)
    time.sleep(0.5)

    region = _select_region()
    if region is None:
        return

    cfg["region"] = list(region)
    _save_config(cfg)

    # 步骤 2: 截取模板
    lines = [
        W.top_border("配置车辆特征", W_VAL),
        W.line(f"{C.GREEN}区域已保存: {region[2]}x{region[3]}{C.RESET}", W_VAL),
        W.line(f"{C.LABEL}正在截取车辆特征...{C.RESET}", W_VAL),
        W.bottom_border(W_VAL),
    ]
    renderer.render(lines)
    time.sleep(0.3)

    _capture_template(region)

    lines = [
        W.top_border("配置车辆特征", W_VAL),
        W.line(f"{C.GREEN}区域已保存: {region[2]}x{region[3]}{C.RESET}", W_VAL),
        W.line(f"{C.GREEN}车辆特征已截取并保存{C.RESET}", W_VAL),
        W.line("", W_VAL),
        W.line(f"{C.GRAY}按任意键返回{C.RESET}", W_VAL),
        W.bottom_border(W_VAL),
    ]
    renderer.render(lines)
    drain_keyboard()
    get_key()


def _run_snipe_loop(
    renderer: Renderer,
    cfg: dict,
    template: np.ndarray,
    region: list[int],
    threshold: float,
    stats: dict,
) -> None:
    """抢车主循环。按 Esc 退出。"""
    drain_keyboard()
    W_VAL = 46
    x, y, w, h = region

    def render_running(status: str = "运行中", color: str = C.GREEN) -> None:
        lines = [
            W.top_border("拍卖行抢车", W_VAL),
            W.line(f"{C.LABEL}状态:{C.RESET} {color}{status}{C.RESET}", W_VAL),
            W.divider("", W_VAL),
            W.line(f"{C.LABEL}尝试次数:{C.RESET} {C.WHITE}{stats['attempts']}{C.RESET}", W_VAL),
            W.line(f"{C.LABEL}发现车辆:{C.RESET} {C.GREEN}{stats['found']}{C.RESET}", W_VAL),
            W.divider("", W_VAL),
            W.line(f"{C.GRAY}Esc 停止{C.RESET}", W_VAL),
            W.bottom_border(W_VAL),
        ]
        renderer.render(lines)

    def render_paused(title: str = "") -> None:
        t = f" ({title})" if title else ""
        lines = [
            W.top_border("拍卖行抢车", W_VAL),
            W.line(f"{C.LABEL}状态:{C.RESET} {C.YELLOW}已暂停{C.RESET}", W_VAL),
            W.line(f"{C.GRAY}游戏窗口未聚焦{t}{C.RESET}", W_VAL),
            W.divider("", W_VAL),
            W.line(f"{C.LABEL}尝试次数:{C.RESET} {C.WHITE}{stats['attempts']}{C.RESET}", W_VAL),
            W.line(f"{C.LABEL}发现车辆:{C.RESET} {C.GREEN}{stats['found']}{C.RESET}", W_VAL),
            W.divider("", W_VAL),
            W.line(f"{C.GRAY}切回游戏自动继续 | Esc 停止{C.RESET}", W_VAL),
            W.bottom_border(W_VAL),
        ]
        renderer.render(lines)

    render_running()

    while True:
        # 检查 Esc (非阻塞)
        if msvcrt.kbhit():
            raw = msvcrt.getch()
            if raw in (b"\xe0", b"\x00"):
                msvcrt.getch()
            elif raw == K.ESC:
                return

        # 检查游戏窗口焦点 — 未聚焦则暂停
        if not _is_game_focused():
            title = _get_foreground_title()
            render_paused(title)
            while not _is_game_focused():
                if msvcrt.kbhit():
                    raw = msvcrt.getch()
                    if raw in (b"\xe0", b"\x00"):
                        msvcrt.getch()
                    elif raw == K.ESC:
                        return
                time.sleep(0.3)
            # 游戏重新聚焦，恢复
            drain_keyboard()
            render_running("恢复运行", C.GREEN)
            time.sleep(0.3)

        stats["attempts"] += 1

        # 1. Enter × 2 — 进入拍卖搜索
        _press("enter")
        _press("enter")
        time.sleep(0.8)  # 等待页面加载

        # 2. 截图比对
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        scr_arr = np.array(screenshot)
        confidence = _match_template(scr_arr, template)

        if confidence >= threshold:
            # 3a. 发现车辆 — Y → ↓ → Enter → Enter
            stats["found"] += 1
            render_running(f"发现车辆! 置信度 {confidence:.1%}", C.GREEN)
            _press("y")
            time.sleep(0.2)
            _press("down")
            time.sleep(0.1)
            _press("enter")
            time.sleep(0.3)
            _press("enter")
            time.sleep(0.5)
        else:
            # 3b. 未发现 — Esc → 立即重试
            _press("esc")
            time.sleep(0.15)

        # 更新显示 (限流，避免渲染过快)
        if stats["attempts"] % 3 == 0:
            render_running()


