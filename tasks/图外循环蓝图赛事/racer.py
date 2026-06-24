"""图外循环蓝图赛事 — 执行按键序列并检测赛事完成。

工作流程:
    1. 用户通过特征库截取赛事完成特征（可选：降低难度特征）
    2. 循环:
       ① PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
          Enter → Enter → Enter
       ② 每隔 N ms 检测赛事准备（loop_until_match 分支判断）:
          - 检测到「降低难度」: ↓ → Enter → 回到②继续检测
          - 检测到「赛事准备」: 继续后续步骤
          - 都未检测到: 重新倒计时再检测
       ③ Enter → 每隔 N ms 检测赛事完成（循环直到识别到）
       ④ Enter → Enter → Esc → 回到①
    3. 实时树状运行图，高亮当前步骤
    4. 游戏失焦自动暂停

执行逻辑由 :class:`core.task_base.BaseTask._execute_tree` 通用树执行器驱动，
本任务只提供步骤树定义。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pyautogui

from udlrtui import Renderer

from core.task_base import BaseTask, Branch, StepConfig
from core.feature_store import FeatureStore

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ── 任务级路径 (tasks/图外循环蓝图赛事/data/) ────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"


# ── RaceLoop 特化定义 ─────────────────────────────────────

class FeatureType(Enum):
    """图外循环蓝图赛事任务所需的特征类型。"""

    RACE_PREP = "race_prep"
    """赛事准备。"""

    RACE_FINISHED = "race_finished"
    """赛事完成。"""

    RACE_LOWER_DIFFICULTY = "race_lower_difficulty"
    """降低难度提示（赛事准备界面偶发弹出的降低难度选项）。"""


SLOT_LABELS: dict[str, str] = {
    "race_prep": "赛事准备",
    "race_finished": "赛事完成",
    "race_lower_difficulty": "降低难度",
}

_DEFAULT_STEPS: list[dict] = [
    {"name": "PageUp", "type": "keypress", "key": "pageup", "delay": 0.05},
    {"name": "PageUp", "type": "keypress", "key": "pageup", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "→", "type": "keypress", "key": "right", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "PageDown", "type": "keypress", "key": "pagedown", "delay": 0.05},
    {"name": "PageDown", "type": "keypress", "key": "pagedown", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {
        "name": "检测赛事准备",
        "type": "match",
        "feature_type": "race_prep",
        "delay": 0.5,
        "loop_until_match": True,
        "branches": [
            {
                "condition": "降低难度",
                "loop": "回到本步骤",
                "node_id": "branch_lower",
                "steps": [
                    {"name": "↓", "type": "keypress", "key": "down", "delay": 0.1, "node_id": "lower_down"},
                    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.3, "node_id": "lower_enter"},
                ],
            },
            {
                "condition": "赛事准备",
                "loop": None,
                "node_id": "branch_prep",
                "steps": [],
            },
        ],
    },
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {
        "name": "检测赛事完成",
        "type": "match",
        "feature_type": "race_finished",
        "delay": 0.5,
    },
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
]


# ══════════════════════════════════════════════════════════
#  RaceLoopTask — BaseTask 子类
# ══════════════════════════════════════════════════════════

class RaceLoopTask(BaseTask):
    """图外循环蓝图赛事任务 — 执行按键序列并检测赛事完成。

    流程::

        ① PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
           Enter → Enter → Enter → 检测赛事准备(loop_until_match) → Enter
        ② 检测赛事准备为循环分支判断:
           - 降低难度 → ↓ → Enter → 回到②
           - 赛事准备 → 继续后续步骤
        ③ 每隔 N ms 检测赛事完成（循环直到识别到）
        ④ Enter → Enter → Esc → 回到①

    所需特征类型 (3 个槽位):
        - ``race_prep`` — 赛事准备（必需）
        - ``race_finished`` — 赛事完成（必需）
        - ``race_lower_difficulty`` — 降低难度（可选，未截取时
          「降低难度」分支永不匹配，任务按原流程运行）
    """

    task_name: str = "图外循环蓝图赛事"
    task_tag: str = "race_loop"
    intro_text: str = (
        "从大地图进入蓝图赛事菜单，自动翻页选择\n"
        "赛事，检测赛事准备后进入比赛，\n"
        "完成赛事后结算并循环。含降低难度分支处理。"
    )

    # ── Setup ──────────────────────────────────────────────

    def _setup(self) -> None:
        """Initialise the FeatureStore for race loop data."""
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
        """Load the step tree from config or defaults.

        ``node_id`` values are filled in from the default steps by
        structural position — older configs saved ``node_id: null``.
        """
        if self.store is not None and self.store.steps:
            try:
                loaded: list[StepConfig] = [
                    RaceLoopTask._step_from_dict(s)
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
                RaceLoopTask._fill_node_ids(
                    loaded_step.children, default_step.children,
                )
            if loaded_step.branches and default_step.branches:
                for lb, db in zip(loaded_step.branches, default_step.branches):
                    if lb.node_id is None and db.node_id:
                        lb.node_id = db.node_id
                    if lb.steps and db.steps:
                        RaceLoopTask._fill_node_ids(lb.steps, db.steps)

    def _default_steps(self) -> list[StepConfig]:
        """Return the hardcoded default step tree.

        Structure::

            PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
            Enter → Enter → Enter → 检测赛事准备(match, loop_until_match) →
            Enter → 检测赛事完成(match, 循环直到识别到) →
            Enter → Enter → Esc (loop: 回到①)

        ``检测赛事准备`` 是一个循环分支判断：每次倒计时后依次检测
        各分支特征，取首个匹配者。``降低难度`` 分支执行 ↓+Enter 后
        回到本步骤继续检测；``赛事准备`` 分支为空，表示直接继续到
        下一步。若本轮无分支匹配则重新倒计时再检测。
        """
        return [
            StepConfig(
                "PageUp", type="keypress", key="pageup",
                delay=0.05, node_id="pageup1",
            ),
            StepConfig(
                "PageUp", type="keypress", key="pageup",
                delay=0.05, node_id="pageup2",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter1",
            ),
            StepConfig(
                "→", type="keypress", key="right",
                delay=0.05, node_id="right1",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter2",
            ),
            StepConfig(
                "PageDown", type="keypress", key="pagedown",
                delay=0.05, node_id="pagedown1",
            ),
            StepConfig(
                "PageDown", type="keypress", key="pagedown",
                delay=0.05, node_id="pagedown2",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter3",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter4",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter5",
            ),
            StepConfig(
                "检测赛事准备",
                type="match",
                feature_type="race_prep",
                delay=0.5,
                node_id="match_prep",
                loop_until_match=True,
                branches=[
                    Branch(
                        "降低难度",
                        [
                            StepConfig(
                                "↓", type="keypress", key="down",
                                delay=0.1, node_id="lower_down",
                            ),
                            StepConfig(
                                "Enter", type="keypress", key="enter",
                                delay=0.3, node_id="lower_enter",
                            ),
                        ],
                        loop="回到本步骤",
                        node_id="branch_lower",
                    ),
                    Branch(
                        "赛事准备",
                        loop=None,
                        node_id="branch_prep",
                    ),
                ],
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter6",
            ),
            StepConfig(
                "检测赛事完成",
                type="match",
                feature_type="race_finished",
                delay=0.5,
                node_id="match_race",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter7",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter8",
            ),
            StepConfig(
                "Esc", type="keypress", key="esc",
                delay=0.05, node_id="esc",
            ),
        ]

    # ── Step definitions ───────────────────────────────────

    def get_steps(self) -> list[StepConfig]:
        """Return the cached step tree (loaded in :meth:`_setup`)."""
        return self._steps_cache

    def get_required_feature_types(self) -> list[FeatureType]:
        """Required feature types for this task."""
        return [FeatureType.RACE_FINISHED]

    # ══════════════════════════════════════════════════════
    #  赛事循环主循环 — 通用执行器
    # ══════════════════════════════════════════════════════

    def _run_core_loop(self) -> None:
        """赛事运行入口 — 直接交给通用树执行器。"""
        self._execute_tree(self._start_node)


# ══════════════════════════════════════════════════════════
#  旧入口 — 保持向后兼容
# ══════════════════════════════════════════════════════════

def run_race_loop(renderer: Renderer) -> None:
    """Legacy entry point — creates and runs a RaceLoopTask."""
    task = RaceLoopTask(renderer)
    task.run()
