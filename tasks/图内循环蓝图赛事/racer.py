"""图内循环蓝图赛事 — 检测赛事开始后按住加速跑完全程。

工作流程:
    1. 用户通过特征库截取开始赛事和完成赛事特征
    2. 循环:
       ① 每隔 N ms 检测开始赛事（循环直到识别到）
       ② Enter → 按下 W → 每隔 N ms 检测完成赛事（循环直到识别到）
       ③ 松开 W → X → Enter → 回到①

执行逻辑由 :class:`core.task_base.BaseTask._execute_tree` 通用树执行器驱动，
本任务只提供步骤树定义。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pyautogui

from udlrtui import Renderer

from core.task_base import BaseTask, Branch, StepConfig
from core.feature_store import FeatureStore, CATEGORY_MONITOR

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 (tasks/图内循环蓝图赛事/data/) ────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"


# ── RaceInner 特化定义 ────────────────────────────────────

class FeatureType(Enum):
    """图内循环蓝图赛事任务所需的特征类型。"""

    RACE_START = "race_start"
    """开始赛事 — 蓝图起跑点就绪。"""

    RACE_FINISHED = "race_finished"
    """完成赛事 — 比赛结束结算画面。"""


SLOT_LABELS: dict[str, str] = {
    "race_start": "开始赛事",
    "race_finished": "完成赛事",
}

_DEFAULT_STEPS: list[dict] = [
    {
        "name": "检测开始赛事",
        "type": "match",
        "feature_type": "race_start",
        "delay": 0.5,
    },
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "按下 W", "type": "press", "key": "w", "delay": 0.01},
    {
        "name": "检测完成赛事",
        "type": "match",
        "feature_type": "race_finished",
        "delay": 0.5,
    },
    {"name": "松开 W", "type": "release", "key": "w", "delay": 0.01},
    {"name": "X", "type": "keypress", "key": "x", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
]


# ══════════════════════════════════════════════════════════
#  RaceInnerTask — BaseTask 子类
# ══════════════════════════════════════════════════════════

class RaceInnerTask(BaseTask):
    """图内循环蓝图赛事任务 — 检测开始后按住 W 跑完全程。

    流程::

        ① 每隔 N ms 检测开始赛事（循环直到识别到）
        ② Enter → 按下 W
        ③ 每隔 N ms 检测完成赛事（循环直到识别到）
        ④ 松开 W → X → Enter → 回到①

    所需特征类型 (2 个槽位):
        - ``race_start`` — 开始赛事（必需）
        - ``race_finished`` — 完成赛事（必需）
    """

    task_name: str = "图内循环蓝图赛事"
    task_tag: str = "race_inner"
    intro_text: str = (
        "赛事准备阶段开始运行。\n"
        "注意：需要在难度设定中开启自动转向。"
    )

    # 循环次数公式：N = (上限 − 下限) ÷ 步长（向下取整）
    loop_formula_template: str = "({0}−{1})÷{2}"
    loop_formula_default_terms: list[int] | None = [999, 357, 20]

    def loop_formula_compute(self, terms: list[int]) -> int:
        """N = (terms[0] − terms[1]) ÷ terms[2]（向下取整）。"""
        if len(terms) >= 3 and terms[2] != 0:
            return (terms[0] - terms[1]) // terms[2]
        return 0

    # ── Setup ──────────────────────────────────────────────

    def _setup(self) -> None:
        """Initialise the FeatureStore for race inner data."""
        self.data_dir: Path = DATA_DIR
        self.store = FeatureStore(
            data_dir=DATA_DIR,
            slot_types=[t.value for t in FeatureType],
            slot_labels=SLOT_LABELS,
            default_steps=_DEFAULT_STEPS,
            slot_categories={t.value: CATEGORY_MONITOR for t in FeatureType},
        )
        self.store.load()
        self.store.load_all_templates()
        self._steps_cache: list[StepConfig] = self._load_steps()
        self.stats = {"attempts": 0, "found": 0}

    def _load_steps(self) -> list[StepConfig]:
        """Load the step tree from config or defaults.

        ``node_id`` values are filled in from the default steps by
        structural position — older configs saved ``node_id: null``.
        """
        if self.store is not None and self.store.steps:
            try:
                loaded: list[StepConfig] = [
                    RaceInnerTask._step_from_dict(s)
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
                RaceInnerTask._fill_node_ids(
                    loaded_step.children, default_step.children,
                )
            if loaded_step.branches and default_step.branches:
                for lb, db in zip(loaded_step.branches, default_step.branches):
                    if lb.node_id is None and db.node_id:
                        lb.node_id = db.node_id
                    if lb.steps and db.steps:
                        RaceInnerTask._fill_node_ids(lb.steps, db.steps)

    def _default_steps(self) -> list[StepConfig]:
        """Return the hardcoded default step tree.

        Structure::

            检测开始赛事(match) → Enter → 按下 W(press) →
            检测完成赛事(match) → 松开 W(release) → X → Enter
        """
        return [
            StepConfig(
                "检测开始赛事",
                type="match",
                feature_type="race_start",
                delay=0.5,
                node_id="match_start",
            ),
            StepConfig(
                "Enter",
                type="keypress",
                key="enter",
                delay=0.05,
                node_id="enter_race",
            ),
            StepConfig(
                "按下 W",
                type="press",
                key="w",
                delay=0.01,
                node_id="press_w",
            ),
            StepConfig(
                "检测完成赛事",
                type="match",
                feature_type="race_finished",
                delay=0.5,
                node_id="match_finish",
            ),
            StepConfig(
                "松开 W",
                type="release",
                key="w",
                delay=0.01,
                node_id="release_w",
            ),
            StepConfig(
                "X",
                type="keypress",
                key="x",
                delay=0.05,
                node_id="key_x",
            ),
            StepConfig(
                "Enter",
                type="keypress",
                key="enter",
                delay=0.05,
                node_id="enter_finish",
            ),
        ]

    # ── Step definitions ───────────────────────────────────

    def get_steps(self) -> list[StepConfig]:
        """Return the cached step tree (loaded in :meth:`_setup`)."""
        return self._steps_cache

    # ══════════════════════════════════════════════════════
    #  赛事循环主循环 — 通用执行器
    # ══════════════════════════════════════════════════════

    def _run_core_loop(self) -> None:
        """赛事运行入口 — 直接交给通用树执行器。"""
        self._execute_tree(self._start_node)


# ══════════════════════════════════════════════════════════
#  旧入口 — 保持向后兼容
# ══════════════════════════════════════════════════════════

def run_race_inner(renderer: Renderer) -> None:
    """Legacy entry point — creates and runs a RaceInnerTask."""
    task = RaceInnerTask(renderer)
    task.run()
