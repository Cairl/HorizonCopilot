"""拍卖场抢车 — 自动检测车辆并购买。

工作流程:
    1. 用户通过按钮选择框选区域 + 截取车辆图片特征
    2. 循环: Enter → Enter → 截图比对
       - 匹配到有车状态: Y → ↓ → Enter → Enter (购买)
       - 匹配到无车状态: Esc → 重新循环
    3. 实时树状运行图，高亮当前步骤
    4. 游戏失焦自动暂停

依赖 :mod:`core.task_base` 的 :class:`BaseTask` 框架和 :class:`FeatureType`。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pyautogui

from udlrtui import C, B, K, Renderer, Navigator, widgets as W
from udlrtui import drain_keyboard
from core.focus import FocusGuard
from core.keyboard import read_key, try_read_key

from .feature_store import FeatureSlot, FeatureStore
from core.task_base import (
    BaseTask,
    Branch,
    FeatureType,
    StepConfig,
    match_feature_slot,
    _ST_DONE,
    _ST_CUR,
    _ST_WAIT,
    _ST_DIM,
)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 (tasks/auction/data/) ──────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"
TEMPLATE_FILE = DATA_DIR / "template.png"

# ── 默认配置 ──────────────────────────────────────────────

_DEFAULTS = {"region": None, "threshold": 0.90}


# ── 配置 (v1/v2 兼容，由 FeatureStore 管理) ───────────────

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


def _capture_template_to(region: tuple[int, int, int, int],
                         filepath: Path) -> None:
    """Capture a screenshot of *region* and save to *filepath* (PNG)."""
    x, y, w, h = region
    img = pyautogui.screenshot(region=(x, y, w, h))
    filepath.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(filepath))


def _capture_template(region: tuple[int, int, int, int]) -> None:
    """Capture a screenshot and save to the default template file."""
    _capture_template_to(region, TEMPLATE_FILE)


# ── 图像匹配 ──────────────────────────────────────────────

def _match_template(screenshot: np.ndarray, template: np.ndarray) -> float:
    """Run OpenCV template matching and return the max confidence (0.0-1.0)."""
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


# ── 多特征匹配 (槽位适配版) ────────────────────────────────

def match_all_features(store) -> tuple[object | None, float]:
    """Match all slots with loaded templates against their regions.

    For each slot in *store* that has ``template_image is not None``
    and ``region is not None``, screenshots its region and runs
    :func:`_match_template`.

    Args:
        store: A :class:`FeatureStore` instance.

    Returns:
        ``(best_slot, best_confidence)`` where *best_slot* is the
        :class:`FeatureSlot` with the highest confidence, or
        ``(None, 0.0)`` if no slot matched or no templates are loaded.
    """
    best_slot = None
    best_confidence: float = 0.0
    for slot in store:
        if slot.template_image is None or slot.region is None:
            continue
        x, y, w, h = slot.region
        try:
            screenshot = pyautogui.screenshot(region=(x, y, w, h))
            scr_np: np.ndarray = np.array(screenshot)
            conf: float = _match_template(scr_np, slot.template_image)
            if conf > best_confidence:
                best_confidence = conf
                best_slot = slot
        except Exception:
            continue
    return best_slot, best_confidence


# ── 按键 ──────────────────────────────────────────────────

def _press(key: str, interval: float = 0.05) -> None:
    """Press a key via pyautogui and wait *interval* seconds."""
    pyautogui.press(key)
    time.sleep(interval)


# ── 动态宽度 ──────────────────────────────────────────────

def _calc_width(content_lines: list[str], title: str, min_w: int = 32) -> int:
    """Calculate the widest display width among content lines and title."""
    from udlrtui import display_width
    max_w = display_width(title) + 6
    for line in content_lines:
        max_w = max(max_w, display_width(line))
    return max(max_w + 6, min_w)


# ══════════════════════════════════════════════════════════
#  AuctionTask — BaseTask 子类
# ══════════════════════════════════════════════════════════

class AuctionTask(BaseTask):
    """拍卖场抢车任务 — 自动搜索和购买车辆。

    所需特征类型 (4 个固定槽位):
        - ``car_present`` — 搜索结果有车
        - ``car_absent`` — 搜索结果无车
        - ``auction_success`` — 购买成功
        - ``auction_failure`` — 购买失败
    """

    task_name: str = "拍卖场抢车"
    task_tag: str = "auction"

    # ── Setup ──────────────────────────────────────────────

    def _setup(self) -> None:
        """Initialise the FeatureStore for auction data."""
        self.data_dir: Path = DATA_DIR
        self.store = FeatureStore(self.data_dir)
        self.store.load()
        self.store.load_all_templates()
        # Load cached steps: from config if it has a full branch tree,
        # otherwise fall back to the hardcoded defaults.
        self._steps_cache: list[StepConfig] = self._load_steps()
        self.nav = Navigator(
            n_items=1 + self._count_nav_steps(self._steps_cache) + 4
        )
        self.stats = {"attempts": 0, "found": 0}
        self._tree_w: int = 40

    def _load_steps(self) -> list[StepConfig]:
        """Load the step tree from config (if it has branches) or defaults.

        The config's ``steps`` field is only loaded if it contains a full
        branch tree (i.e. at least one step with ``branches``).  This
        avoids replacing the rich default tree with the minimal 4-step
        list written by older config versions.

        ``node_id`` values are filled in from the default steps by
        structural position — older configs saved ``node_id: null``
        because they were serialised before ``node_id`` existed, which
        broke runtime status updates (``_run_core_loop`` looks up nodes
        by ``node_id``).
        """
        if self.store is not None and self.store.steps:
            has_branches = any(s.get("branches") for s in self.store.steps)
            if has_branches:
                try:
                    loaded: list[StepConfig] = [
                        AuctionTask._step_from_dict(s)
                        for s in self.store.steps
                    ]
                    self._fill_node_ids(loaded, self._default_steps())
                    return loaded
                except Exception:
                    pass
        return self._default_steps()

    @staticmethod
    def _fill_node_ids(
        loaded: list[StepConfig],
        defaults: list[StepConfig],
    ) -> None:
        """Fill in missing ``node_id`` from *defaults* by structural position.

        Walks *loaded* and *defaults* in parallel (depth-first, same
        traversal order as :meth:`_iter_steps`).  When a loaded node has
        ``node_id is None`` and the corresponding default has a non-empty
        ``node_id``, the default's value is copied over.  Branches are
        matched by position as well.

        This is a backward-compatibility fix for configs saved before
        ``node_id`` was introduced.
        """
        for loaded_step, default_step in zip(loaded, defaults):
            if loaded_step.node_id is None and default_step.node_id:
                loaded_step.node_id = default_step.node_id
            # Recurse into children
            if loaded_step.children and default_step.children:
                AuctionTask._fill_node_ids(
                    loaded_step.children, default_step.children,
                )
            # Recurse into branches (matched by position)
            if loaded_step.branches and default_step.branches:
                for lb, db in zip(loaded_step.branches, default_step.branches):
                    if lb.node_id is None and db.node_id:
                        lb.node_id = db.node_id
                    if lb.steps and db.steps:
                        AuctionTask._fill_node_ids(lb.steps, db.steps)

    def _default_steps(self) -> list[StepConfig]:
        """Return the hardcoded default step tree (full branch tree).

        Each node carries a ``node_id`` matching the key used by
        :meth:`_run_core_loop` for runtime status updates.
        """
        return [
            StepConfig(
                "Enter (搜索)", type="keypress", key="enter", delay=0.05,
                node_id="enter_search",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter", delay=0.05,
                node_id="enter",
            ),
            StepConfig(
                "等待加载", type="wait", delay=0.8, node_id="wait",
            ),
            StepConfig(
                "判断",
                type="match",
                feature_type="car_present/car_absent",
                delay=0.0,
                node_id="match_car",
                branches=[
                    Branch(
                        "有车状态",
                        [
                            StepConfig(
                                "Y", type="keypress", key="y", delay=0.2,
                                node_id="y",
                            ),
                            StepConfig(
                                "\u2193",  # ↓
                                type="keypress",
                                key="down",
                                delay=0.1,
                                node_id="down",
                            ),
                            StepConfig(
                                "Enter", type="keypress", key="enter",
                                delay=0.3, node_id="enter_buy1",
                            ),
                            StepConfig(
                                "Enter", type="keypress", key="enter",
                                delay=0.5, node_id="enter_buy2",
                            ),
                            StepConfig(
                                "判断",
                                type="match",
                                feature_type="auction_success/auction_failure",
                                delay=0.0,
                                node_id="match_result",
                                branches=[
                                    Branch(
                                        "抢车成功", loop="结束",
                                        node_id="branch_success",
                                    ),
                                    Branch(
                                        "抢车失败",
                                        [
                                            StepConfig(
                                                "Enter", type="keypress",
                                                key="enter", delay=0.15,
                                                node_id="fail_enter",
                                            ),
                                            StepConfig(
                                                "Esc", type="keypress",
                                                key="esc", delay=0.15,
                                                node_id="fail_esc1",
                                            ),
                                            StepConfig(
                                                "Esc", type="keypress",
                                                key="esc", delay=0.15,
                                                node_id="fail_esc2",
                                            ),
                                        ],
                                        loop="回到\u2460",
                                        node_id="branch_fail",
                                    ),
                                ],
                            ),
                        ],
                        node_id="branch_car_yes",
                    ),
                    Branch(
                        "无车状态",
                        [
                            StepConfig(
                                "Esc", type="keypress", key="esc", delay=0.15,
                                node_id="nocar_esc",
                            ),
                        ],
                        loop="回到\u2460",
                        node_id="branch_nocar",
                    ),
                ],
            ),
        ]

    # ── Step definitions ───────────────────────────────────

    def get_steps(self) -> list[StepConfig]:
        """Return the cached step tree (loaded in :meth:`_setup`).

        The cache ensures adjusted delays persist within the session
        and are written back to config on each adjustment (see
        :meth:`~core.task_base.BaseTask._adjust_step_delay`).
        """
        return self._steps_cache

    def get_required_feature_types(self) -> list[FeatureType]:
        """Required feature types for this task."""
        return [
            FeatureType.CAR_PRESENT,
            FeatureType.CAR_ABSENT,
            FeatureType.AUCTION_SUCCESS,
            FeatureType.AUCTION_FAILURE,
        ]

    # ── Single-step execution ──────────────────────────────

    def execute_step(self, step: StepConfig) -> bool:
        """Execute a single step; always returns True."""
        if step.type == "keypress" and step.key:
            _press(step.key, step.delay)
        elif step.type == "wait":
            time.sleep(step.delay)
        # match steps are handled in _run_core_loop
        return True

    # ══════════════════════════════════════════════════════
    #  抢车主循环
    # ══════════════════════════════════════════════════════

    # 分支 dim 集合（按 flat 节点 key）——未走的分支整组置灰
    _DIM_CAR_YES: set[str] = {
        "branch_car_yes",
        "y", "down", "enter_buy1", "enter_buy2",
        "match_result", "branch_success", "branch_fail",
        "fail_enter", "fail_esc1", "fail_esc2",
    }
    _DIM_NOCAR: set[str] = {"branch_nocar", "nocar_esc"}
    _DIM_SUCCESS: set[str] = {"branch_success"}
    _DIM_FAIL: set[str] = {
        "branch_fail",
        "fail_enter", "fail_esc1", "fail_esc2",
    }

    def _run_core_loop(self) -> None:
        """核心抢车循环 — 全自动搜索 / 购买迭代。

        流程::

            while True:
                ① Enter
                ② Enter
                ③ 截图识别(有车/无车)
                   - 无车状态 → Esc → 回到①
                   - 有车状态 → 继续④
                   - 都不匹配 → Esc → 回到①
                ④ Y → ↓ → Enter → Enter
                ⑤ 截图识别(成功/失败)
                   - 抢车成功 → break (返回 idle)
                   - 抢车失败 → Enter → Esc → Esc → 回到①

        界面保持 idle 三区域布局（开始按钮 + 执行图 + 特征库），
        执行图锁定为只读，通过 ``runtime_status`` 实时显示每个节点
        的状态（当前高亮 / 已完成打勾 / 未走分支置灰）。
        """
        if self.store is None:
            return

        drain_keyboard()

        # 设置运行状态为"运行中"（红色"停止运行"）
        self._run_state = "running"

        # 构建 node_id → StepConfig/Branch 映射，用于运行时状态更新
        node_map: dict[str, StepConfig | Branch] = {}
        self._build_node_map(self._steps_cache, node_map)

        # 计算面板宽度（与 idle 布局一致，含状态标记宽度）
        width_samples: list[str] = ["停止运行"]
        self._collect_width_samples(
            self._steps_cache, "", width_samples, running=True,
        )
        for slot in self.store:
            label = self.store.SLOT_LABELS.get(
                slot.feature_type, slot.feature_type
            )
            width_samples.append(f"{label} [截取] [删除]")
        self._tree_w = self._calc_width(width_samples, self.task_name, 42)

        guard: FocusGuard = self._make_guard(self._tree_w)

        def _set(k: str, status: str) -> None:
            node = node_map.get(k)
            if node is not None:
                node.runtime_status = status

        def _dim(keys: set[str]) -> None:
            for k in keys:
                node = node_map.get(k)
                if node is not None:
                    node.runtime_status = _ST_DIM

        def _render() -> None:
            self.render_idle(run_state=self._run_state)

        def _press_step(k: str, key_name: str) -> None:
            """标记点击 *k* 为当前→渲染→睡眠延迟→按键→标记完成。"""
            node = node_map.get(k)
            delay: float = node.delay if node is not None else 0.05
            _set(k, _ST_CUR)
            _render()
            time.sleep(delay)
            pyautogui.press(key_name)
            _set(k, _ST_DONE)

        while True:
            key = try_read_key()
            if key is not None and key in (K.ESC, K.BS, K.ENTER):
                return

            if not guard.check_or_pause():
                return

            self.stats["attempts"] += 1
            # 每轮重置运行时状态
            self._reset_runtime_status(self._steps_cache)

            # ── ① Enter (搜索) ──
            _press_step("enter_search", "enter")

            # ── ② Enter ──
            _press_step("enter", "enter")

            # ── 等待加载 ──
            _set("wait", _ST_CUR)
            _render()
            _wait_node = node_map.get("wait")
            _wait_delay = _wait_node.delay if _wait_node is not None else 0.8
            time.sleep(_wait_delay)
            _set("wait", _ST_DONE)

            # ── ③ 截图识别(有车/无车) ──
            _set("match_car", _ST_CUR)
            _render()

            key = try_read_key()
            if key is not None and key in (K.ESC, K.BS, K.ENTER):
                return

            _, absent_conf = match_feature_slot(self.store, "car_absent")
            fallback: float = self.store.settings.get(
                "global_threshold_fallback", 0.85
            )

            if absent_conf >= fallback:
                # 无车状态 → Esc → 回到①
                _set("match_car", _ST_DONE)
                _dim(self._DIM_CAR_YES)
                _set("branch_nocar", _ST_DONE)
                _press_step("nocar_esc", "esc")
                continue

            _, present_conf = match_feature_slot(self.store, "car_present")

            if present_conf >= fallback:
                # 有车状态 → 继续④
                _set("match_car", _ST_DONE)
                _set("branch_car_yes", _ST_DONE)
                _dim(self._DIM_NOCAR)

                # ── ④ Y → ↓ → Enter → Enter ──
                _press_step("y", "y")
                _press_step("down", "down")
                _press_step("enter_buy1", "enter")
                _press_step("enter_buy2", "enter")

                # ── ⑤ 截图识别(成功/失败) ──
                _set("match_result", _ST_CUR)
                _render()

                key = try_read_key()
                if key is not None and key in (K.ESC, K.BS, K.ENTER):
                    return

                _, success_conf = match_feature_slot(
                    self.store, "auction_success"
                )
                _, fail_conf = match_feature_slot(
                    self.store, "auction_failure"
                )

                if success_conf >= fallback:
                    # 抢车成功 → 结束
                    _set("match_result", _ST_DONE)
                    _set("branch_success", _ST_DONE)
                    _dim(self._DIM_FAIL)
                    self.stats["found"] += 1
                    _render()
                    time.sleep(1.5)
                    return

                elif fail_conf >= fallback:
                    # 抢车失败 → Enter → Esc → Esc → 回到①
                    _set("match_result", _ST_DONE)
                    _set("branch_fail", _ST_DONE)
                    _dim(self._DIM_SUCCESS)
                    _press_step("fail_enter", "enter")
                    _press_step("fail_esc1", "esc")
                    _press_step("fail_esc2", "esc")
                    continue

                else:
                    # 无明确结果 → 视为失败
                    _set("match_result", _ST_DONE)
                    _dim(self._DIM_SUCCESS | self._DIM_FAIL)
                    _press("esc", 0.15)
                    continue

            else:
                # 都不匹配 → Esc → 回到①
                _set("match_car", _ST_DONE)
                _dim(self._DIM_CAR_YES | self._DIM_NOCAR)
                _press("esc", 0.15)
                continue


# ══════════════════════════════════════════════════════════
#  旧入口 — 保持向后兼容
# ══════════════════════════════════════════════════════════

def run_auction_sniper(renderer: Renderer) -> None:
    """Legacy entry point — creates and runs an AuctionTask.

    Args:
        renderer: UdlrTui ``Renderer`` instance.
    """
    task = AuctionTask(renderer)
    task.run()
