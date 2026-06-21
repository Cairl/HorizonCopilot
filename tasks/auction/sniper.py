"""拍卖场抢车 — 自动检测车辆并购买。

工作流程:
    1. 用户通过按钮选择框选区域 + 截取车辆图片特征
    2. 循环: Enter → Enter → 截图比对
       - 匹配到有车状态: Y → ↓ → Enter → Enter (购买)
       - 匹配到无车状态: Esc → 重新循环
    3. 实时树状运行图，高亮当前步骤
    4. 游戏失焦自动暂停

依赖 :mod:`core.task_base` 的 :class:`BaseTask` 框架和 :class:`StepConfig`。
"""

from __future__ import annotations

import ctypes
import time
from enum import Enum
from pathlib import Path

import pyautogui

from udlrtui import K, Renderer, Navigator
from udlrtui import drain_keyboard, try_get_key

from core.focus import FocusGuard
from core.task_base import (
    BaseTask,
    Branch,
    StepConfig,
    _ST_DONE,
    _ST_CUR,
    _ST_DIM,
)
from core.feature_store import FeatureStore

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 (tasks/auction/data/) ──────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"


# ── Auction 特化定义 ──────────────────────────────────────

class FeatureType(Enum):
    """拍卖场抢车任务所需的特征类型。"""

    CAR_PRESENT = "car_present"
    """搜索结果有车。"""

    CAR_ABSENT = "car_absent"
    """搜索结果无车。"""

    AUCTION_SUCCESS = "auction_success"
    """购买成功。"""

    AUCTION_FAILURE = "auction_failure"
    """购买失败。"""


SLOT_LABELS: dict[str, str] = {
    "car_present": "有车状态",
    "car_absent": "无车状态",
    "auction_success": "抢车成功",
    "auction_failure": "抢车失败",
}

_DEFAULT_STEPS: list[dict] = [
    {"name": "Enter (搜索)", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {
        "name": "截图识别",
        "type": "match",
        "feature_type": "car_present/car_absent",
        "delay": 0.1,
    },
]


# ── 按键 ──────────────────────────────────────────────────

class _PauseExit(Exception):
    """用户在暂停期间退出运行（Esc/Enter），用于跳出按键流程。"""


def _press(key: str, interval: float = 0.05) -> None:
    """Press a key via pyautogui and wait *interval* seconds."""
    pyautogui.press(key)
    time.sleep(interval)


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
        self.store = FeatureStore(
            data_dir=DATA_DIR,
            slot_types=[t.value for t in FeatureType],
            slot_labels=SLOT_LABELS,
            default_steps=_DEFAULT_STEPS,
        )
        self.store.load()
        self.store.load_all_templates()
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
            if loaded_step.children and default_step.children:
                AuctionTask._fill_node_ids(
                    loaded_step.children, default_step.children,
                )
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
                "Enter", type="keypress", key="enter", delay=0.05,
                node_id="enter2",
            ),
            StepConfig(
                "判断",
                type="match",
                feature_type="car_present/car_absent",
                delay=0.1,
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
                                delay=0.1,
                                node_id="match_result",
                                branches=[
                                    Branch(
                                        "\u62a2\u8f66\u6210\u529f", loop="\u7ed3\u675f",
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
                ③ Enter
                ④ 截图识别(有车/无车)
                   - 无车状态 → Esc → 回到①
                   - 有车状态 → 继续④
                   - 都不匹配 → Esc → 回到①
                ⑤ Y → ↓ → Enter → Enter
                ⑥ 截图识别(成功/失败)
                   - 抢车成功 → break (返回 idle)
                   - 抢车失败 → Enter → Esc → Esc → 回到①

        界面保持 idle 三区域布局（开始按钮 + 执行图 + 特征库），
        执行图锁定为只读，通过 ``runtime_status`` 实时显示每个节点
        的状态（当前高亮 / 已完成打勾 / 未走分支置灰）。
        """
        if self.store is None:
            return

        drain_keyboard()

        # 检查大写锁定，没开就开启（拍卖场搜索需要）
        _caps_was_on = bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
        if not _caps_was_on:
            pyautogui.press("capslock")
            time.sleep(0.05)

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
        from core.task_base import calc_width
        self._tree_w = calc_width(width_samples, self.task_name, 42)

        guard: FocusGuard = self._make_guard(self._tree_w)

        # 等待游戏窗口完全切换到前台（SetForegroundWindow 异步生效）
        # 最多重试 10 次，每次间隔 50ms，总计 500ms
        _focus_ok: bool = False
        for _ in range(10):
            if guard.check():
                _focus_ok = True
                break
            time.sleep(0.05)
        if not _focus_ok:
            return

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

        def _countdown(node, delay: float) -> None:
            """倒计时显示剩余毫秒，每 50ms 更新渲染。

            用 ``time.monotonic`` 计算实际经过时间，确保总等待时间
            不受 sleep 精度影响。期间检查用户中断和焦点丢失。
            """
            if delay <= 0:
                return
            total_ms: int = int(delay * 1000)
            step_ms: int = 50
            start: float = time.monotonic()
            deadline: float = start + delay
            while True:
                elapsed: float = time.monotonic() - start
                remaining: int = max(0, total_ms - int(elapsed * 1000))
                if remaining <= 0:
                    break
                node.runtime_remaining_ms = remaining
                _render()
                to_deadline: float = deadline - time.monotonic()
                if to_deadline <= 0:
                    break
                time.sleep(min(step_ms / 1000.0, to_deadline))
                key = try_get_key()
                if key is not None and key in (K.ESC, K.BS, K.ENTER):
                    raise _PauseExit()
                if not guard.check():
                    raise _PauseExit()
            node.runtime_remaining_ms = None

        def _press_step(k: str, key_name: str) -> None:
            """标记点击 *k* 为当前→倒计时渲染→按键→标记完成。

            按键前检查焦点，失焦则抛出 :class:`_PauseExit` 终止运行。
            """
            node = node_map.get(k)
            delay: float = node.delay if node is not None else 0.05
            _set(k, _ST_CUR)
            _countdown(node, delay)
            # 按键前检查焦点，避免失焦时把按键送到控制台
            if not guard.check():
                raise _PauseExit()
            pyautogui.press(key_name)
            _set(k, _ST_DONE)

        def _press_guarded(key_name: str, interval: float = 0.15) -> None:
            """无状态跟踪的按键，同样在按键前检查焦点。"""
            if not guard.check():
                raise _PauseExit()
            pyautogui.press(key_name)
            time.sleep(interval)

        while True:
            key = try_get_key()
            if key is not None and key in (K.ESC, K.BS, K.ENTER):
                return

            if not guard.check():
                return

            try:
                self.stats["attempts"] += 1
                # 每轮重置运行时状态
                self._reset_runtime_status(self._steps_cache)

                # ── ① Enter (搜索) ──
                _press_step("enter_search", "enter")

                # ── ② Enter ──
                _press_step("enter", "enter")

                # ── ③ Enter ──
                _press_step("enter2", "enter")

                # ── ④ 截图识别(有车/无车) — 倒计时等待画面稳定后截图 ──
                _set("match_car", _ST_CUR)
                _render()
                _match_car_node = node_map.get("match_car")
                if _match_car_node is not None:
                    _countdown(_match_car_node, _match_car_node.delay)

                key = try_get_key()
                if key is not None and key in (K.ESC, K.BS, K.ENTER):
                    return

                _, absent_conf = self.store.match_slot("car_absent")
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

                _, present_conf = self.store.match_slot("car_present")

                if present_conf >= fallback:
                    # 有车状态 → 继续④
                    _set("match_car", _ST_DONE)
                    _dim(self._DIM_NOCAR)
                    _set("branch_car_yes", _ST_DONE)

                    # ── ⑤ Y → ↓ → Enter → Enter ──
                    _press_step("y", "y")
                    _press_step("down", "down")
                    _press_step("enter_buy1", "enter")
                    _press_step("enter_buy2", "enter")

                    # ── ⑥ 截图识别(成功/失败) — 倒计时等待画面稳定后截图 ──
                    _set("match_result", _ST_CUR)
                    _render()
                    _match_res_node = node_map.get("match_result")
                    if _match_res_node is not None:
                        _countdown(_match_res_node, _match_res_node.delay)

                    key = try_get_key()
                    if key is not None and key in (K.ESC, K.BS, K.ENTER):
                        return

                    _, success_conf = self.store.match_slot("auction_success")
                    _, fail_conf = self.store.match_slot("auction_failure")

                    if success_conf >= fallback:
                        # 抢车成功 → 结束
                        _set("match_result", _ST_DONE)
                        _dim(self._DIM_FAIL)
                        _set("branch_success", _ST_DONE)
                        self.stats["found"] += 1
                        _render()
                        time.sleep(1.5)
                        return

                    elif fail_conf >= fallback:
                        # 抢车失败 → Enter → Esc → Esc → 回到①
                        _set("match_result", _ST_DONE)
                        _dim(self._DIM_SUCCESS)
                        _set("branch_fail", _ST_DONE)
                        _press_step("fail_enter", "enter")
                        _press_step("fail_esc1", "esc")
                        _press_step("fail_esc2", "esc")
                        continue

                    else:
                        # 无明确结果 → 视为失败
                        _set("match_result", _ST_DONE)
                        _dim(self._DIM_SUCCESS | self._DIM_FAIL)
                        _press_guarded("esc")
                        continue

                else:
                    # 都不匹配 → Esc → 回到①
                    _set("match_car", _ST_DONE)
                    _dim(self._DIM_CAR_YES | self._DIM_NOCAR)
                    _press_guarded("esc")
                    continue

            except _PauseExit:
                # 用户在暂停期间退出（Esc/Enter），结束运行
                return


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
