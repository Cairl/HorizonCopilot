"""拍卖场抢车 — 自动检测车辆并购买。

工作流程:
    1. 用户通过特征库截取车辆图片特征
    2. 循环: Enter → Enter → Enter → 截图比对
       - 匹配到有车状态: Y → ↓ → Enter → Enter
         截图比对: 抢车成功 → 结束; 抢车失败 → Enter → Esc → Esc → 回到①
       - 匹配到无车状态: Esc → 回到①
       - 都不匹配: Esc → 回到①
    3. 实时树状运行图，高亮当前步骤
    4. 游戏失焦自动暂停

执行逻辑由 :class:`core.task_base.BaseTask._execute_tree` 通用树执行器驱动，
本任务只提供步骤树定义和 CapsLock 预处理钩子。
"""

from __future__ import annotations

import ctypes
import time
from enum import Enum
from pathlib import Path

import pyautogui

from udlrtui import Renderer

from core.task_base import BaseTask, Branch, StepConfig
from core.feature_store import FeatureStore

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 (tasks/拍卖场抢车/data/) ──────────────────────

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
        self.stats = {"attempts": 0, "found": 0}

    def _load_steps(self) -> list[StepConfig]:
        """Load the step tree from config (if it has branches) or defaults.

        The config's ``steps`` field is only loaded if it contains a full
        branch tree (i.e. at least one step with ``branches``).  This
        avoids replacing the rich default tree with the minimal 4-step
        list written by older config versions.

        ``node_id`` values are filled in from the default steps by
        structural position — older configs saved ``node_id: null``
        because they were serialised before ``node_id`` existed.
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
        """Fill in missing ``node_id`` from *defaults* by structural position."""
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
        """Return the hardcoded default step tree (full branch tree)."""
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
                fallback_key="esc",
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
                                fallback_key="esc",
                                node_id="match_result",
                                branches=[
                                    Branch(
                                        "\u62a2\u8f66\u6210\u529f",
                                        loop="\u7ed3\u675f",
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
        """Return the cached step tree (loaded in :meth:`_setup`)."""
        return self._steps_cache

    def get_required_feature_types(self) -> list[FeatureType]:
        """Required feature types for this task."""
        return [
            FeatureType.CAR_PRESENT,
            FeatureType.CAR_ABSENT,
            FeatureType.AUCTION_SUCCESS,
            FeatureType.AUCTION_FAILURE,
        ]

    # ══════════════════════════════════════════════════════
    #  抢车主循环 — CapsLock 预处理 + 通用执行器
    # ══════════════════════════════════════════════════════

    def _run_core_loop(self) -> None:
        """拍卖场抢车运行入口。

        搜索需要大写锁定开启，因此运行前检测并按需开启 CapsLock，
        然后交给 :meth:`BaseTask._execute_tree` 通用树执行器驱动整个流程。
        """
        # 检查大写锁定，没开就开启（拍卖场搜索需要）
        _caps_was_on = bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
        if not _caps_was_on:
            pyautogui.press("capslock")
            time.sleep(0.05)

        self._execute_tree(self._start_node)


# ══════════════════════════════════════════════════════════
#  旧入口 — 保持向后兼容
# ══════════════════════════════════════════════════════════

def run_auction_sniper(renderer: Renderer) -> None:
    """Legacy entry point — creates and runs an AuctionTask."""
    task = AuctionTask(renderer)
    task.run()
