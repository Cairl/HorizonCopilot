"""购买斯巴鲁抽奖 — 四阶段长程自动化任务。

工作流程:
    1. 买车: 菜单导航进入车展，点击斯巴鲁品牌，购买指定车辆
    2. 上车: 翻页选择购买的车辆并驾驶
    3. 抽奖: 利用车辆技能点进行抽奖
    4. 卖车: 菜单导航找到车辆并卖出

所有步骤通过 :class:`core.task_base.BaseTask._execute_tree` 驱动。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pyautogui

from udlrtui import Renderer

from core.task_base import BaseTask, Branch, StepConfig
from core.feature_store import FeatureStore, CATEGORY_LOCATOR

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 ────────────────────────────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"


# ── Subaru 特化定义 ───────────────────────────────────────

class FeatureType(Enum):
    """购买斯巴鲁抽奖任务所需的特征类型。"""

    SUBARU_FACTORY_LOWER = "subaru_factory_lower"
    """斯巴鲁牌商标（小写）。第 9 行用。"""

    SUBARU_FACTORY_UPPER = "subaru_factory_upper"
    """斯巴鲁牌商标（大写）。上车/卖车阶段用。"""

    SUBARU_CAR = "subaru_car"
    """斯巴鲁车辆标识。"""


SLOT_LABELS: dict[str, str] = {
    "subaru_factory_lower": "斯巴鲁牌(小写)",
    "subaru_factory_upper": "斯巴鲁牌(大写)",
    "subaru_car": "斯巴鲁车",
}

_DEFAULT_STEPS: list[dict] = [
    # ══════ 1. 买车 ══════
    {"name": "左", "type": "keypress", "key": "left", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "右", "type": "keypress", "key": "right", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Backspace", "type": "keypress", "key": "backspace", "delay": 0.05},
    {
        "name": "判断斯巴鲁牌小写",
        "type": "match",
        "feature_type": "subaru_factory_lower",
        "delay": 1.0,
        "loop_until_match": True,
        "branches": [
          {"condition": "斯巴鲁牌(小写)", "steps": []},
          {"condition": "如果失败", "loop": "回到本步骤", "steps": [
            {"name": "上", "type": "keypress", "key": "up", "delay": 0.05}
          ]}
        ]
    },
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "Space", "type": "keypress", "key": "space", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    # ══════ 2. 上车 ══════
    {"name": "PageDown", "type": "keypress", "key": "pagedown", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Backspace", "type": "keypress", "key": "backspace", "delay": 0.05},
    {
        "name": "判断斯巴鲁牌大写",
        "type": "match",
        "feature_type": "subaru_factory_upper",
        "delay": 1.0,
        "loop_until_match": True,
        "branches": [
          {"condition": "斯巴鲁牌(大写)", "steps": []},
          {"condition": "如果失败", "loop": "回到本步骤", "steps": [
            {"name": "上", "type": "keypress", "key": "up", "delay": 0.05}
          ]}
        ]
    },
    {
        "name": "左键斯巴鲁车",
        "type": "match",
        "feature_type": "subaru_car",
        "delay": 0.5,
        "loop_until_match": True,
        "branches": [
          {"condition": "斯巴鲁车", "steps": []},
          {"condition": "如果失败", "loop": "回到本步骤", "steps": [
            {"name": "右", "type": "keypress", "key": "right", "delay": 0.05}
          ]}
        ]
    },
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    # ══════ 3. 抽奖 ══════
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    {"name": "PageDown", "type": "keypress", "key": "pagedown", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "右", "type": "keypress", "key": "right", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "上", "type": "keypress", "key": "up", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "上", "type": "keypress", "key": "up", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "上", "type": "keypress", "key": "up", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "左", "type": "keypress", "key": "left", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    # ══════ 4. 卖车 ══════
    {"name": "上", "type": "keypress", "key": "up", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "右", "type": "keypress", "key": "right", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    {"name": "PageDown", "type": "keypress", "key": "pagedown", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Backspace", "type": "keypress", "key": "backspace", "delay": 0.05},
    {
        "name": "判断斯巴鲁牌大写",
        "type": "match",
        "feature_type": "subaru_factory_upper",
        "delay": 1.0,
        "loop_until_match": True,
        "branches": [
          {"condition": "斯巴鲁牌(大写)", "steps": []},
          {"condition": "如果失败", "loop": "回到本步骤", "steps": [
            {"name": "上", "type": "keypress", "key": "up", "delay": 0.05}
          ]}
        ]
    },
    {
        "name": "左键斯巴鲁车",
        "type": "match",
        "feature_type": "subaru_car",
        "delay": 0.5,
        "loop_until_match": True,
        "branches": [
          {"condition": "斯巴鲁车", "steps": []},
          {"condition": "如果失败", "loop": "回到本步骤", "steps": [
            {"name": "右", "type": "keypress", "key": "right", "delay": 0.05}
          ]}
        ]
    },
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "下", "type": "keypress", "key": "down", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
    {"name": "PageUp", "type": "keypress", "key": "pageup", "delay": 0.05},
]


# ══════════════════════════════════════════════════════════
#  SubaruTask — BaseTask 子类
# ══════════════════════════════════════════════════════════

class SubaruTask(BaseTask):
    """购买斯巴鲁抽奖 — 四阶段全自动循环。

    流程::

        ① 买车: 菜单导航 → 左键斯巴鲁厂 → 购买车辆
        ② 上车: 翻页 → 左键斯巴鲁厂/斯巴鲁车 → 驾驶
        ③ 抽奖: 技能点抽奖 → 共三次 → 结算
        ④ 卖车: 菜单导航 → 左键斯巴鲁厂/斯巴鲁车 → 卖出 → 回到①

    所需特征类型 (2 个槽位):
        - ``subaru_factory`` — 斯巴鲁厂商标识（必需）
        - ``subaru_car`` — 斯巴鲁车辆标识（必需）
    """

    task_name: str = "购买斯巴鲁抽奖"
    task_tag: str = "subaru"
    intro_text: str = (
        "购买/抽奖/卖车全自动四阶段循环。\n"
        "需要截取斯巴鲁厂和斯巴鲁车两个特征。"
    )

    # ── Setup ──────────────────────────────────────────────

    def _setup(self) -> None:
        """Initialise the FeatureStore for subaru data."""
        self.data_dir: Path = DATA_DIR
        self.store = FeatureStore(
            data_dir=DATA_DIR,
            slot_types=[t.value for t in FeatureType],
            slot_labels=SLOT_LABELS,
            default_steps=_DEFAULT_STEPS,
            slot_categories={t.value: CATEGORY_LOCATOR for t in FeatureType},
        )
        self.store.load()
        self.store.load_all_templates()
        self._steps_cache: list[StepConfig] = self._load_steps()
        self.stats = {"attempts": 0, "found": 0}

    def _load_steps(self) -> list[StepConfig]:
        """Load the step tree from config or defaults."""
        if self.store is not None and self.store.steps:
            try:
                loaded: list[StepConfig] = [
                    SubaruTask._step_from_dict(s)
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
                SubaruTask._fill_node_ids(
                    loaded_step.children, default_step.children,
                )
            if loaded_step.branches and default_step.branches:
                for lb, db in zip(loaded_step.branches, default_step.branches):
                    if lb.node_id is None and db.node_id:
                        lb.node_id = db.node_id
                    if lb.steps and db.steps:
                        SubaruTask._fill_node_ids(lb.steps, db.steps)

    def _default_steps(self) -> list[StepConfig]:
        """Return the hardcoded default step tree (64 steps, 4 phases)."""
        _KP = lambda n, k, d=0.05: StepConfig(n, type="keypress", key=k, delay=d)
        _CM = lambda n, f, d=0.5: StepConfig(
            n, type="click_match", feature_type=f, delay=d,
        )
        steps = [
            # ── 1. 买车 ──
            _KP("左", "left"),                                  #  0
            _KP("Enter", "enter"),                               #  1
            _KP("右", "right"),                                  #  2
            _KP("Enter", "enter"),                               #  3
            _KP("下", "down"),                                   #  4
            _KP("Enter", "enter"),                               #  5
            _KP("Backspace", "backspace"),                       #  6
            StepConfig(
                "判断斯巴鲁牌小写", type="match",
                feature_type="subaru_factory_lower",
                delay=1.0, loop_until_match=True,
                branches=[
                    Branch("斯巴鲁牌(小写)", []),
                    Branch("如果失败", [
                        _KP("上", "up"),
                    ], loop="回到本步骤"),
                ],
            ),                                                  #  7
            _KP("下", "down"),                                   # 10
            _KP("Space", "space"),                              # 10
            _KP("下", "down"),                                  # 11
            _KP("Enter", "enter"),                              # 12
            _KP("Enter", "enter"),                              # 13
            _KP("Enter", "enter"),                              # 14
            _KP("Esc", "esc"),                                  # 15
            _KP("Esc", "esc"),                                  # 16
            _KP("Esc", "esc"),                                  # 17
            # ── 2. 上车 ──
            _KP("PageDown", "pagedown"),                        # 18
            _KP("Enter", "enter"),                              # 19
            _KP("Backspace", "backspace"),                      # 20
            # 全屏匹配斯巴鲁牌(大写)，找到点击，如果失败按 UP 重试
            StepConfig(
                "判断斯巴鲁牌大写", type="match",
                feature_type="subaru_factory_upper",
                delay=1.0, loop_until_match=True,
                branches=[
                    Branch("斯巴鲁牌(大写)", []),
                    Branch("如果失败", [
                        _KP("上", "up"),
                    ], loop="回到本步骤"),
                ],
            ),                                                  # 22
            StepConfig(
                "左键斯巴鲁车", type="match",
                feature_type="subaru_car",
                delay=0.5, loop_until_match=True,
                branches=[
                    Branch("斯巴鲁车", []),
                    Branch("如果失败", [
                        _KP("右", "right"),
                    ], loop="回到本步骤"),
                ],
            ),                                                  # 23
            _KP("Enter", "enter"),                              # 25
            _KP("Enter", "enter"),                              # 26
            # ── 3. 抽奖 ──
            _KP("Esc", "esc"),                                  # 28
            _KP("PageDown", "pagedown"),                        # 27
            _KP("下", "down"),                                  # 28
            _KP("Enter", "enter"),                              # 29
            _KP("Enter", "enter"),                              # 30
            _KP("右", "right"),                                 # 31
            _KP("Enter", "enter"),                              # 32
            _KP("上", "up"),                                    # 33
            _KP("Enter", "enter"),                              # 34
            _KP("上", "up"),                                    # 35
            _KP("Enter", "enter"),                              # 36
            _KP("上", "up"),                                    # 37
            _KP("Enter", "enter"),                              # 38
            _KP("左", "left"),                                  # 39
            _KP("Enter", "enter"),                              # 40
            _KP("Enter", "enter"),                              # 41
            _KP("Enter", "enter"),                              # 42
            _KP("Esc", "esc"),                                  # 43
            # ── 4. 卖车 ──
            _KP("上", "up"),                                    # 44
            _KP("Enter", "enter"),                              # 45
            _KP("右", "right"),                                 # 46
            _KP("Enter", "enter"),                              # 47
            _KP("Enter", "enter"),                              # 48
            _KP("Esc", "esc"),                                  # 49
            _KP("PageDown", "pagedown"),                        # 50
            _KP("Enter", "enter"),                              # 51
            _KP("Backspace", "backspace"),                      # 52
            StepConfig(
                "判断斯巴鲁牌大写", type="match",
                feature_type="subaru_factory_upper",
                delay=1.0, loop_until_match=True,
                branches=[
                    Branch("斯巴鲁牌(大写)", []),
                    Branch("如果失败", [
                        _KP("上", "up"),
                    ], loop="回到本步骤"),
                ],
            ),                                                  # 54
            StepConfig(
                "左键斯巴鲁车", type="match",
                feature_type="subaru_car",
                delay=0.5, loop_until_match=True,
                branches=[
                    Branch("斯巴鲁车", []),
                    Branch("如果失败", [
                        _KP("右", "right"),
                    ], loop="回到本步骤"),
                ],
            ),                                                  # 55
            _KP("Enter", "enter"),                              # 57
            _KP("下", "down"),                                  # 58
            _KP("下", "down"),                                  # 59
            _KP("下", "down"),                                  # 59
            _KP("下", "down"),                                  # 60
            _KP("Enter", "enter"),                              # 61
            _KP("下", "down"),                                  # 62
            _KP("Enter", "enter"),                              # 63
            _KP("Esc", "esc"),                                  # 64
            _KP("PageUp", "pageup"),                             # 65
        ]
        # Auto-generate node_ids so runtime highlighting works.
        # The numbering mirrors the #comment on each line above.
        for i, step in enumerate(steps, start=1):
            if step.node_id is None:
                step.node_id = f"subaru_s{i:02d}"

        return steps

    # ── Step definitions ───────────────────────────────────

    def get_steps(self) -> list[StepConfig]:
        """Return the cached step tree."""
        return self._steps_cache

    # ── 主循环 ─────────────────────────────────────────────

    def _run_core_loop(self) -> None:
        """入口 — 直接交给通用树执行器。"""
        self._execute_tree(self._start_node)


# ══════════════════════════════════════════════════════════
#  旧入口 — 保持向后兼容
# ══════════════════════════════════════════════════════════

def run_subaru(renderer: Renderer) -> None:
    """Legacy entry point — creates and runs a SubaruTask."""
    task = SubaruTask(renderer)
    task.run()
