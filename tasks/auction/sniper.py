"""拍卖行抢车 — 自动检测车辆并购买。

工作流程:
    1. 用户通过按钮选择框选区域 + 截取车辆图片特征
    2. 循环: Enter → Enter → 截图比对
       - 匹配到车辆: Y → ↓ → Enter → Enter (购买)
       - 未匹配: Esc → 重新循环
    3. 实时树状运行图，高亮当前步骤
    4. 游戏失焦自动暂停
"""

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pyautogui

from udlrtui import C, B, K, Renderer, Navigator, widgets as W
from udlrtui import get_key, drain_keyboard
from core.focus import FocusGuard
from core.keyboard import read_key, try_read_key

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 (tasks/auction/data/) ──────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"
TEMPLATE_FILE = DATA_DIR / "template.png"

# ── 默认配置 ──────────────────────────────────────────────

_DEFAULTS = {"region": None, "threshold": 0.90}


# ── 配置 ──────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(_DEFAULTS)


def _save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── 区域选择器 ────────────────────────────────────────────

def _select_region() -> tuple[int, int, int, int] | None:
    """全屏覆盖，用户拖拽框选区域。返回 (x, y, w, h)。"""
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
    result = {}

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


def _capture_template(region: tuple[int, int, int, int]) -> None:
    x, y, w, h = region
    img = pyautogui.screenshot(region=(x, y, w, h))
    DATA_DIR.mkdir(exist_ok=True)
    img.save(str(TEMPLATE_FILE))


# ── 图像匹配 ──────────────────────────────────────────────

def _match_template(screenshot: np.ndarray, template: np.ndarray) -> float:
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
    pyautogui.press(key)
    time.sleep(interval)


# ── 动态宽度 ──────────────────────────────────────────────

def _calc_width(content_lines: list[str], title: str, min_w: int = 32) -> int:
    from udlrtui import display_width
    max_w = display_width(title) + 6
    for line in content_lines:
        max_w = max(max_w, display_width(line))
    return max(max_w + 6, min_w)


# ══════════════════════════════════════════════════════════
#  拍卖行抢车主入口
# ══════════════════════════════════════════════════════════

def run_auction_sniper(renderer: Renderer) -> None:
    cfg = _load_config()
    region = cfg.get("region")
    threshold = cfg.get("threshold", 0.90)
    template = _load_template(region)

    renderer.reset()
    drain_keyboard()
    stats = {"attempts": 0, "found": 0}

    while True:
        has_tpl = template is not None

        # ── 渲染配置页 ──
        cfg_str = (f"{C.GREEN}已配置{C.RESET} ({region[2]}x{region[3]})"
                   if has_tpl else f"{C.RED}未配置{C.RESET}")
        btns = ["配置特征", "开始抢车"] if has_tpl else ["配置特征"]
        nav = Navigator(n_items=len(btns))

        def _render_cfg():
            lines_content = [
                f"{C.LABEL}车辆特征:{C.RESET} {cfg_str}",
                f"{C.LABEL}匹配置信度:{C.RESET} {C.WHITE}\u2265 {threshold:.0%}{C.RESET}",
            ]
            w = _calc_width(
                lines_content + btns + ["Esc 返回主菜单"],
                "拍卖行抢车", 36)
            lines = [
                W.top_border("拍卖行抢车", w),
                W.line(lines_content[0], w),
                W.line(lines_content[1], w),
                W.divider("", w),
            ]
            parts = []
            for j, btn in enumerate(btns):
                if j == nav.index:
                    parts.append(f"{C.BOLD}{C.WHITE}[{C.RESET}"
                                 f"{C.BOLD}{C.TEAL}{btn}{C.RESET}"
                                 f"{C.BOLD}{C.WHITE}]{C.RESET}")
                else:
                    parts.append(f"{C.TEAL}{btn}{C.RESET}")
            lines.append(W.line("  ".join(parts), w))
            lines.append(W.divider("", w))
            lines.append(W.line(f"{C.GRAY}Esc 返回主菜单{C.RESET}", w))
            lines.append(W.bottom_border(w))
            renderer.render(lines)

        _render_cfg()

        # ── 按键处理 ──
        drain_keyboard()
        key = read_key()

        if key == b"\x00":  # F2
            _do_configure(renderer, cfg)
            cfg = _load_config()
            region = cfg.get("region")
            template = _load_template(region)
            renderer.reset()
            continue

        if key in (K.ESC, K.BS):
            renderer.reset()
            return

        if nav.handle(key):
            continue

        if key == K.ENTER:
            sel = nav.index
            if sel == 0:  # 配置特征 — 框选 + 截取一步完成
                _do_configure(renderer, cfg)
                cfg = _load_config()
                region = cfg.get("region")
                template = _load_template(region)
                renderer.reset()
            elif sel == 1 and has_tpl:  # 开始抢车
                _run_snipe_loop(renderer, cfg, template, region,
                                threshold, stats)


def _load_template(region):
    """加载模板图片，返回 RGB numpy 数组或 None。"""
    if region and TEMPLATE_FILE.exists():
        img = cv2.imread(str(TEMPLATE_FILE))
        if img is not None:
            return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return None


# ══════════════════════════════════════════════════════════
#  配置流程
# ══════════════════════════════════════════════════════════

def _do_configure(renderer: Renderer, cfg: dict) -> None:
    renderer.reset()
    w = _calc_width(
        ["请在屏幕上拖拽框选有车时的特征区域", "框选完毕自动截取，Esc 取消"],
        "配置车辆特征", 36)
    lines = [
        W.top_border("配置车辆特征", w),
        W.line(f"{C.YELLOW}请在屏幕上拖拽框选有车时的特征区域{C.RESET}", w),
        W.line(f"{C.GRAY}框选完毕自动截取，Esc 取消{C.RESET}", w),
        W.bottom_border(w),
    ]
    renderer.render(lines)
    time.sleep(0.5)

    region = _select_region()
    if region is None:
        return

    cfg["region"] = list(region)
    _save_config(cfg)

    lines = [
        W.top_border("配置车辆特征", w),
        W.line(f"{C.GREEN}区域已保存: {region[2]}x{region[3]}{C.RESET}", w),
        W.line(f"{C.LABEL}正在截取车辆特征...{C.RESET}", w),
        W.bottom_border(w),
    ]
    renderer.render(lines)
    time.sleep(0.3)
    _capture_template(region)

    lines = [
        W.top_border("配置车辆特征", w),
        W.line(f"{C.GREEN}区域已保存: {region[2]}x{region[3]}{C.RESET}", w),
        W.line(f"{C.GREEN}车辆特征已截取并保存{C.RESET}", w),
        W.line("", w),
        W.line(f"{C.GRAY}按任意键返回{C.RESET}", w),
        W.bottom_border(w),
    ]
    renderer.render(lines)
    drain_keyboard()
    get_key()


# ══════════════════════════════════════════════════════════
#  树状运行图
# ══════════════════════════════════════════════════════════

_ST_DONE = "done"
_ST_CUR = "current"
_ST_WAIT = "waiting"


def _build_actions(confidence: float | None, found: bool) -> list[dict]:
    actions = [
        {"name": "Enter (搜索)", "delay": 0.0, "status": _ST_WAIT,
         "children": [
             {"name": "Enter", "delay": 0.05, "status": _ST_WAIT},
             {"name": "Enter", "delay": 0.05, "status": _ST_WAIT},
         ]},
        {"name": "等待加载", "delay": 0.8, "status": _ST_WAIT},
        {"name": "截图比对", "delay": 0.0, "status": _ST_WAIT},
    ]
    if found and confidence is not None:
        actions.append({
            "name": f"购买 (置信度 {confidence:.1%})", "delay": 0.0,
            "status": _ST_WAIT,
            "children": [
                {"name": "Y", "delay": 0.2, "status": _ST_WAIT},
                {"name": "\u2193", "delay": 0.1, "status": _ST_WAIT},
                {"name": "Enter", "delay": 0.3, "status": _ST_WAIT},
                {"name": "Enter", "delay": 0.5, "status": _ST_WAIT},
            ]})
    else:
        actions.append(
            {"name": "未发现 — Esc", "delay": 0.15, "status": _ST_WAIT})
    return actions


def _flatten_actions(actions: list[dict]) -> list[dict]:
    seq = []
    for act in actions:
        act["_level"] = 0
        seq.append(act)
        for child in act.get("children", []):
            child["_level"] = 1
            seq.append(child)
    return seq


def _render_tree(renderer: Renderer, title: str, flat: list[dict],
                 current_idx: int, stats: dict, w: int) -> None:
    lines = [W.top_border(title, w)]
    lines.append(W.line(
        f"{C.LABEL}尝试:{C.RESET} {C.WHITE}{stats['attempts']}"
        f"{C.RESET}  {C.LABEL}发现:{C.RESET} "
        f"{C.GREEN}{stats['found']}{C.RESET}", w))
    lines.append(W.divider("", w))

    show_start = max(0, current_idx - 3)
    show_end = min(len(flat), current_idx + 5)

    for item in flat[show_start:show_end]:
        level = item.get("_level", 0)
        status = item["status"]
        indent = "  " * level if level else ""
        delay = item["delay"]

        if status == _ST_DONE:
            mark = f"{C.GREEN}\u2713{C.RESET}"
            name_c = f"{C.GRAY}{item['name']}{C.RESET}"
        elif status == _ST_CUR:
            mark = f"{C.WHITE}{C.BOLD}\u25b6{C.RESET}"
            name_c = f"{C.WHITE}{C.BOLD}{item['name']}{C.RESET}"
        else:
            mark = f"{C.GRAY}\u25cb{C.RESET}"
            name_c = f"{C.GRAY}{item['name']}{C.RESET}"

        delay_s = (f" {C.GRAY}{delay:.2f}s{C.RESET}" if delay > 0 else "")
        lines.append(W.line(f"{indent}{mark} {name_c}{delay_s}", w))

    lines.append(W.divider("", w))
    lines.append(W.line(f"{C.GRAY}Esc 停止{C.RESET}", w))
    lines.append(W.bottom_border(w))
    renderer.render(lines)


# ══════════════════════════════════════════════════════════
#  抢车主循环
# ══════════════════════════════════════════════════════════

def _run_snipe_loop(renderer, cfg, template, region, threshold, stats):
    drain_keyboard()
    x, y, w, h = region
    tree_w = max(_calc_width([], "拍卖行抢车", 36), 40)

    guard = FocusGuard(
        on_pause=lambda t: _render_paused(renderer, stats, t, tree_w),
        on_resume=lambda: _render_status(renderer, stats, "恢复运行",
                                         C.GREEN, tree_w),
    )

    while True:
        key = try_read_key()
        if key is not None and key in (K.ESC, K.BS):
            return

        if not guard.check_or_pause():
            return

        stats["attempts"] += 1

        actions = _build_actions(None, False)
        flat = _flatten_actions(actions)

        def _set(idx, status):
            if 0 <= idx < len(flat):
                flat[idx]["status"] = status

        def _render(idx):
            _render_tree(renderer, "拍卖行抢车", flat, idx, stats, tree_w)

        step = 0

        # ── Enter × 2 ──
        _set(step, _ST_CUR); _render(step)
        _press("enter"); _set(step, _ST_DONE)
        step += 1

        _set(step, _ST_CUR); _render(step)
        _press("enter"); _set(step, _ST_DONE)
        step += 1

        _set(0, _ST_DONE)

        # ── 等待加载 ──
        step = 3
        _set(step, _ST_CUR); _render(step)
        time.sleep(0.8); _set(step, _ST_DONE)

        # ── 截图比对 ──
        step = 4
        _set(step, _ST_CUR); _render(step)
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        confidence = _match_template(np.array(screenshot), template)
        _set(step, _ST_DONE)

        found = confidence >= threshold
        actions = _build_actions(confidence, found)
        flat = _flatten_actions(actions)
        for i in range(5):
            _set(i, _ST_DONE)

        if found:
            stats["found"] += 1
            for i in range(4):
                idx = 5 + i
                _set(idx, _ST_CUR); _render(idx)
                _press(["y", "down", "enter", "enter"][i],
                       [0.2, 0.1, 0.3, 0.5][i])
                _set(idx, _ST_DONE)
        else:
            _set(5, _ST_CUR); _render(5)
            _press("esc", 0.15)
            _set(5, _ST_DONE)

        _render(len(flat) - 1)
        time.sleep(0.1)


def _render_status(renderer, stats, text, color, w):
    lines = [
        W.top_border("拍卖行抢车", w),
        W.line(f"{C.LABEL}状态:{C.RESET} {color}{text}{C.RESET}", w),
        W.divider("", w),
        W.line(f"{C.LABEL}尝试次数:{C.RESET} {C.WHITE}{stats['attempts']}{C.RESET}", w),
        W.line(f"{C.LABEL}发现车辆:{C.RESET} {C.GREEN}{stats['found']}{C.RESET}", w),
        W.bottom_border(w),
    ]
    renderer.render(lines)


def _render_paused(renderer, stats, title, w):
    t = f" ({title})" if title else ""
    lines = [
        W.top_border("拍卖行抢车", w),
        W.line(f"{C.LABEL}状态:{C.RESET} {C.YELLOW}已暂停{C.RESET}", w),
        W.line(f"{C.GRAY}游戏窗口未聚焦{t}{C.RESET}", w),
        W.divider("", w),
        W.line(f"{C.LABEL}尝试次数:{C.RESET} {C.WHITE}{stats['attempts']}{C.RESET}", w),
        W.line(f"{C.LABEL}发现车辆:{C.RESET} {C.GREEN}{stats['found']}{C.RESET}", w),
        W.divider("", w),
        W.line(f"{C.GRAY}切回游戏自动继续 | Esc 停止{C.RESET}", w),
        W.bottom_border(w),
    ]
    renderer.render(lines)
