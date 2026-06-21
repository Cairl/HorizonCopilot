"""图外循环蓝图赛事 — 执行按键序列并检测比赛完成。

工作流程:
    1. 用户通过按钮选择框选区域 + 截取比赛完成特征
    2. 循环:
       ① PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
          Enter → Enter → Enter
       ② 每隔 N ms 检测比赛完成（循环直到识别到）
       ③ Enter → Enter → Esc → 回到①
    3. 实时树状运行图，高亮当前步骤
    4. 游戏失焦自动暂停

依赖 :mod:`core.task_base` 的 :class:`BaseTask` 框架和 :class:`StepConfig`。
"""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path

import pyautogui

from udlrtui import K, Renderer, Navigator
from udlrtui import drain_keyboard, get_key, try_get_key

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

# ── 任务级路径 (tasks/图外循环蓝图赛事/data/) ────────────────────

_TASK_DIR = Path(__file__).parent
DATA_DIR = _TASK_DIR / "data"


# ── RaceLoop 特化定义 ─────────────────────────────────────

class FeatureType(Enum):
    """图外循环蓝图赛事任务所需的特征类型。"""

    RACE_FINISHED = "race_finished"
    """比赛完成。"""


SLOT_LABELS: dict[str, str] = {
    "race_finished": "比赛完成",
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
        "name": "检测比赛完成",
        "type": "match",
        "feature_type": "race_finished",
        "delay": 0.5,
    },
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Esc", "type": "keypress", "key": "esc", "delay": 0.05},
]


# ── 按键 ──────────────────────────────────────────────────

class _PauseExit(Exception):
    """用户在暂停期间退出运行（Esc/Enter），用于跳出按键流程。"""


def _press(key: str, interval: float = 0.05) -> None:
    """Press a key via pyautogui and wait *interval* seconds."""
    pyautogui.press(key)
    time.sleep(interval)


# ══════════════════════════════════════════════════════════
#  RaceLoopTask — BaseTask 子类
# ══════════════════════════════════════════════════════════

class RaceLoopTask(BaseTask):
    """图外循环蓝图赛事任务 — 执行按键序列并检测比赛完成。

    流程::

        ① PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
           Enter → Enter → Enter
        ② 每隔 N ms 检测比赛完成（循环直到识别到）
        ③ Enter → Enter → Esc → 回到①

    所需特征类型 (1 个固定槽位):
        - ``race_finished`` — 比赛完成
    """

    task_name: str = "图外循环蓝图赛事"
    task_tag: str = "race_loop"

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
        self.nav = Navigator(
            n_items=1 + self._count_nav_steps(self._steps_cache) + len(self.store)
        )
        self.stats = {"attempts": 0, "found": 0}
        self._tree_w: int = 40

    def _load_steps(self) -> list[StepConfig]:
        """Load the step tree from config or defaults.

        The config's ``steps`` field is loaded if it exists and is
        non-empty; otherwise the hardcoded defaults are used.

        ``node_id`` values are filled in from the default steps by
        structural position — older configs saved ``node_id: null``
        because they were serialised before ``node_id`` existed.
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
        """Fill in missing ``node_id`` from *defaults* by structural position.

        Walks *loaded* and *defaults* in parallel (depth-first, same
        traversal order as :meth:`_iter_steps`).  When a loaded node has
        ``node_id is None`` and the corresponding default has a non-empty
        ``node_id``, the default's value is copied over.  Branches are
        matched by position as well.
        """
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
        """Return the hardcoded default step tree (flat, no branches).

        Structure::

            PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
            Enter → Enter → Enter → 检测比赛完成(match, 循环直到识别到) →
            Enter → Enter → Esc (loop: 回到①)

        Each node carries a ``node_id`` matching the key used by
        :meth:`_run_core_loop` for runtime status updates.
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
                "检测比赛完成",
                type="match",
                feature_type="race_finished",
                delay=0.5,
                node_id="match_race",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter6",
            ),
            StepConfig(
                "Enter", type="keypress", key="enter",
                delay=0.05, node_id="enter7",
            ),
            StepConfig(
                "Esc", type="keypress", key="esc",
                delay=0.05, node_id="esc",
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
        return [FeatureType.RACE_FINISHED]

    # ── Single-step execution ──────────────────────────────

    def execute_step(self, step: StepConfig) -> bool:
        """Execute a single step; always returns True."""
        if step.type == "keypress" and step.key:
            _press(step.key, step.delay)
        elif step.type == "hold" and step.key:
            pyautogui.keyDown(step.key)
            time.sleep(step.delay)
            pyautogui.keyUp(step.key)
        elif step.type == "wait":
            time.sleep(step.delay)
        # match steps are handled in _run_core_loop
        return True

    # ══════════════════════════════════════════════════════
    #  赛事循环主循环
    # ══════════════════════════════════════════════════════

    def _run_core_loop(self) -> None:
        """核心赛事循环 — 执行按键序列，检测比赛完成后继续。

        流程::

            while True:
                ① PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
                   Enter → Enter → Enter
                ② 每隔 N ms 检测比赛完成（循环直到识别到）
                ③ Enter → Enter → Esc → 回到①

        界面保持 idle 三区域布局（开始按钮 + 执行图 + 特征库），
        执行图锁定为只读，通过 ``runtime_status`` 实时显示每个节点
        的状态（当前高亮 / 已完成打勾）。
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

        def _render() -> None:
            self.render_idle(run_state=self._run_state)

        def _countdown(node, delay: float) -> None:
            """倒计时显示剩余毫秒，每 50ms 更新渲染。

            用 ``time.monotonic`` 计算实际经过时间，确保总等待时间
            不受 sleep 精度影响。期间检查用户中断和焦点丢失。

            当 *delay* <= 0 时进入暂停模式：运行状态归位（开始按钮
            变回"开始运行"、所有节点状态清零、光标聚焦到当前 0ms 行），
            阻塞等待用户按 Enter 继续（Esc/Backspace 终止运行）。
            """
            if delay <= 0:
                # 0ms = 暂停：运行状态归位（开始按钮变回"开始运行"）、
                # 所有节点状态清零，光标聚焦到当前 0ms 行，
                # 等待用户按 Enter 继续
                self._run_state = "idle"
                self._reset_runtime_status(self._steps_cache)
                # 定位当前 node 在可导航节点列表中的索引（+1 跳过开始按钮）
                nav_idx: int = 1
                for i, (s, _t) in enumerate(self._iter_nav_steps(self._steps_cache)):
                    if s is node:
                        nav_idx = i + 1
                        break
                self.nav.index = nav_idx
                _render()
                # 暂停期间允许用户移动光标 / 调整延迟（不锁住），
                # Enter 继续运行，Esc/Backspace 终止运行
                steps = self._steps_cache
                slot_start = 1 + self._count_nav_steps(steps)
                while True:
                    key = get_key()
                    if key in (K.ESC, K.BS):
                        raise _PauseExit()
                    if key == K.ENTER:
                        break
                    idx = self.nav.index
                    if key in (K.UP, K.DOWN):
                        self.nav.handle(key)
                        if (self.nav.index != idx
                                and self.nav.index >= slot_start):
                            self._slot_action = 0
                    elif key in (K.LEFT, K.RIGHT,
                                 K.SHIFT_LEFT, K.SHIFT_RIGHT):
                        if 1 <= idx < slot_start:
                            delta = (1000 if key in (K.SHIFT_LEFT,
                                     K.SHIFT_RIGHT) else 10)
                            sign = (-1 if key in (K.LEFT,
                                    K.SHIFT_LEFT) else 1)
                            self._adjust_step_delay(idx - 1, sign * delta)
                    _render()
                self._run_state = "running"
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

        def _hold_step(k: str, key_name: str) -> None:
            """标记按住 *k* 为当前→按下→倒计时(按住时长)→抬起→标记完成。

            按下和抬起前都检查焦点。倒计时期间若失焦或用户中断，
            通过 ``finally`` 保证按键抬起，再抛出 :class:`_PauseExit`。
            """
            node = node_map.get(k)
            delay: float = node.delay if node is not None else 2.0
            _set(k, _ST_CUR)
            # 按下前检查焦点
            if not guard.check():
                raise _PauseExit()
            pyautogui.keyDown(key_name)
            try:
                _countdown(node, delay)
            finally:
                # 无论倒计时是否被中断，都保证抬起按键
                pyautogui.keyUp(key_name)
            _set(k, _ST_DONE)

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

                # ── ① 前置按键序列 ──
                # PageUp → PageUp → Enter → → → Enter → PageDown → PageDown →
                # Enter → Enter → Enter
                _press_step("pageup1", "pageup")
                _press_step("pageup2", "pageup")
                _press_step("enter1", "enter")
                _press_step("right1", "right")
                _press_step("enter2", "enter")
                _press_step("pagedown1", "pagedown")
                _press_step("pagedown2", "pagedown")
                _press_step("enter3", "enter")
                _press_step("enter4", "enter")
                _press_step("enter5", "enter")

                # ── ② 循环截图识别(比赛完成) — 直到检测到比赛完成 ──
                while True:
                    _set("match_race", _ST_CUR)
                    _render()
                    _match_node = node_map.get("match_race")
                    if _match_node is not None:
                        _countdown(_match_node, _match_node.delay)

                    key = try_get_key()
                    if key is not None and key in (K.ESC, K.BS, K.ENTER):
                        return

                    fallback: float = self.store.settings.get(
                        "global_threshold_fallback", 0.85
                    )
                    _, finished_conf = self.store.match_slot("race_finished")

                    if finished_conf >= fallback:
                        # 比赛完成 → 继续③
                        _set("match_race", _ST_DONE)
                        break

                    # 比赛未完成 → 回到②（重新截图识别）
                    # match_race 保持 _ST_CUR，直接重新倒计时截图
                    continue

                # ── ③ 后置按键序列 ──
                # Enter → Enter → Esc → 回到①
                _press_step("enter6", "enter")
                _press_step("enter7", "enter")
                _press_step("esc", "esc")
                continue

            except _PauseExit:
                # 用户在暂停期间退出（Esc/Enter），结束运行
                return


# ══════════════════════════════════════════════════════════
#  旧入口 — 保持向后兼容
# ══════════════════════════════════════════════════════════

def run_race_loop(renderer: Renderer) -> None:
    """Legacy entry point — creates and runs a RaceLoopTask.

    Args:
        renderer: UdlrTui ``Renderer`` instance.
    """
    task = RaceLoopTask(renderer)
    task.run()
