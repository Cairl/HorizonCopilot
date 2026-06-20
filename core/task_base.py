"""Core task framework — abstract base class for all automation tasks.

Provides :class:`FeatureType` enum, :class:`StepConfig` dataclass,
:func:`match_feature_slot` utility, and the :class:`BaseTask`
abstract base class using the template method pattern for
idle / running state management.

Usage::

    class MyTask(BaseTask):
        task_name = "我的任务"
        task_tag = "my_task"

        def _setup(self):
            self.data_dir = Path(__file__).parent / "data"
            self.store = FeatureStore(self.data_dir)
            self.store.load()
            self.store.load_all_templates()
            self.nav = Navigator(n_items=1 + len(steps) + 4)
            steps = self.get_steps()
            self.stats = {"attempts": 0, "found": 0}

        def get_steps(self) -> list[StepConfig]:
            return [...]

        def get_required_feature_types(self) -> list[FeatureType]:
            return [...]

        def execute_step(self, step: StepConfig) -> bool:
            ...

        def _run_core_loop(self):
            ...
"""

from __future__ import annotations

import msvcrt
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import numpy as np
import pyautogui

from udlrtui import C, K, Renderer, Navigator, widgets as W
from udlrtui import drain_keyboard

from core.focus import FocusGuard
from core.keyboard import read_key, try_read_key

if TYPE_CHECKING:
    from tasks.auction.feature_store import FeatureSlot, FeatureStore


# ── FeatureType ────────────────────────────────────────────

class FeatureType(Enum):
    """Enumerated types for feature classification.

    Each value corresponds to a distinct visual state the automation
    needs to recognise on screen.
    """

    CAR_PRESENT = "car_present"
    """Vehicle is present in auction search results."""

    CAR_ABSENT = "car_absent"
    """No vehicle found (empty auction result)."""

    AUCTION_SUCCESS = "auction_success"
    """Successfully purchased the vehicle."""

    AUCTION_FAILURE = "auction_failure"
    """Auction failed (outbid / timed out)."""


# ── StepConfig ────────────────────────────────────────────

@dataclass
class StepConfig:
    """Configuration for a single execution step.

    Attributes:
        name: Human-readable label (e.g. ``"Enter (搜索)"``).
        type: Step type — ``"keypress"`` | ``"wait"`` | ``"match"``.
        key: :mod:`pyautogui` key name (only for ``"keypress"`` steps).
        delay: Seconds to wait after executing this step.
        feature_type: Feature type string for ``"match"`` steps
            (e.g. ``"car_present/car_absent"``).
        children: Optional ordered sub-steps executed after this step.
        branches: Optional conditional branches — each :class:`Branch`
            is taken when its condition matches the recognition result.
            Mutually exclusive with ``children`` in practice.
        node_id: Stable identifier for runtime status lookup (e.g.
            ``"enter_search"``).  Set by subclasses in :meth:`get_steps`.
        runtime_status: Runtime-only status (``""`` / ``_ST_DONE`` /
            ``_ST_CUR`` / ``_ST_DIM``).  Reset before each run; not
            serialised to config.
    """

    name: str
    type: str = "keypress"
    key: str | None = None
    delay: float = 0.05
    feature_type: str | None = None
    children: list[StepConfig] | None = None
    branches: list[Branch] | None = None
    node_id: str | None = None
    runtime_status: str = ""


@dataclass
class Branch:
    """A conditional branch in the execution tree.

    Attributes:
        condition: Human-readable condition label shown in the tree
            (e.g. ``"有车"``, ``"无车"``, ``"成功"``, ``"失败"``).
        steps: Ordered steps to execute when this branch is taken.
        loop: Loop-back label shown after the condition (e.g.
            ``"回到①"``, ``"结束"``) or ``None`` for none.
        node_id: Stable identifier for runtime status lookup.
        runtime_status: Runtime-only status (not serialised).
    """

    condition: str
    steps: list[StepConfig] = field(default_factory=list)
    loop: str | None = None
    node_id: str | None = None
    runtime_status: str = ""


# ── Feature slot label mapping ────────────────────────────

_SLOT_LABELS: dict[str, str] = {
    "car_present": "有车状态",
    "car_absent": "无车状态",
    "auction_success": "抢车成功",
    "auction_failure": "抢车失败",
}


def feature_type_label(type_str: str | None) -> str:
    """Return a short Chinese label for a feature type string."""
    if type_str is None:
        return ""
    return _SLOT_LABELS.get(type_str, type_str)


# ── Action type label mapping ─────────────────────────────

_ACTION_LABELS: dict[str, str] = {
    "keypress": "点击",
    "click": "点击",
    "press": "按下",
    "release": "抬起",
    "wait": "等待",
    "match": "判断",
}


def action_label(type_str: str) -> str:
    """Return the Chinese action label for a step type.

    Mapping::

        keypress / click → 点击
        press            → 按下
        release          → 抬起
        wait             → 等待
        match            → 判断
    """
    return _ACTION_LABELS.get(type_str, type_str)


# ── match_feature_slot ────────────────────────────────────

def match_feature_slot(
    store: FeatureStore,
    feature_type: str,
) -> tuple[Any | None, float]:
    """Match a single feature slot against its screen region.

    Screenshots the slot's region and runs template matching against
    its loaded template image.

    Args:
        store: :class:`~tasks.auction.feature_store.FeatureStore`
            with loaded template images.
        feature_type: Slot type string — one of ``"car_present"``,
            ``"car_absent"``, ``"auction_success"``, ``"auction_failure"``.

    Returns:
        ``(slot, confidence)`` — the :class:`FeatureSlot` and its match
        confidence, or ``(None, 0.0)`` if the slot has no template
        or matching failed.
    """
    # Lazy import to avoid circular dependency (sniper imports BaseTask)
    from tasks.auction.sniper import _match_template as _mt

    slot = store.slots.get(feature_type)
    if slot is None or not slot.has_template() or slot.region is None:
        return None, 0.0

    x, y, w, h = slot.region
    try:
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        scr_np: np.ndarray = np.array(screenshot)
        conf: float = _mt(scr_np, slot.template_image)
        return slot, conf
    except Exception:
        return None, 0.0


# ── Tree rendering constants ──────────────────────────────

_ST_DONE = "done"
_ST_CUR = "current"
_ST_WAIT = "waiting"
_ST_DIM = "dimmed"  # branch not taken (greyed out)


# ══════════════════════════════════════════════════════════
#  BaseTask — Abstract Base Class
# ══════════════════════════════════════════════════════════

class BaseTask(ABC):
    """Abstract base for all automation tasks.

    Implements the template-method pattern with a two-state
    (idle / running) main loop.  Subclasses provide:
      - :meth:`_setup` — initialisation
      - :meth:`get_steps` — step definitions
      - :meth:`get_required_feature_types` — required types
      - :meth:`execute_step` — single-step execution
      - :meth:`_run_core_loop` — full running-state flow

    Attributes:
        task_name: Human-readable task name.
        task_tag: Unique task identifier (used for routing).
        renderer: UdlrTui ``Renderer`` instance.
        store: :class:`~tasks.auction.feature_store.FeatureStore`
            (created by :meth:`_setup`).
        nav: Single Navigator for the entire idle screen
            (start button + 4 feature slots + execution steps).
        state: ``"idle"`` or ``"running"``.
        focus: ``"features"`` or ``"steps"`` (Tab-switchable).
        stats: Dict with ``attempts`` and ``found`` counters.
    """

    task_name: str = ""
    task_tag: str = ""

    # ── Abstract methods ──────────────────────────────────

    @abstractmethod
    def _setup(self) -> None:
        """Initialise the task (create store, load config, load templates).

        Called once when :meth:`run` starts.  Subclasses **must**
        set ``self.store``, ``self.nav``,
        and ``self.stats`` here.
        """
        ...

    @abstractmethod
    def get_steps(self) -> list[StepConfig]:
        """Return the execution step definitions for this task."""
        ...

    @abstractmethod
    def get_required_feature_types(self) -> list[FeatureType]:
        """Return the feature types this task needs to operate."""
        ...

    @abstractmethod
    def execute_step(self, step: StepConfig) -> bool:
        """Execute a single step.  Return ``True`` to continue, ``False`` to stop."""
        ...

    @abstractmethod
    def _run_core_loop(self) -> None:
        """Core automation loop — called when entering running state.

        This is where the main game interaction happens (e.g. the
        auction-snipe iteration).  Should return when the loop
        completes or is interrupted by the user.
        """
        ...

    # ── Concrete initialisation ───────────────────────────

    def __init__(self, renderer: Renderer) -> None:
        """Initialise navigators to empty (overwritten by :meth:`_setup`)."""
        self.renderer: Renderer = renderer
        self.store: FeatureStore | None = None
        self.nav: Navigator = Navigator(n_items=0)
        self.state: str = "idle"
        self.stats: dict = {"attempts": 0, "found": 0}
        self._guard: FocusGuard | None = None
        # Per-slot action: 0 = 截取, 1 = 删除
        self._slot_action: int = 0
        # 运行状态: "idle" / "running" / "waiting"
        # idle=未启动(绿色"开始运行")
        # running=运行中(红色"停止运行")
        # waiting=游戏未聚焦(黄色"等待运行")
        self._run_state: str = "idle"

    # ── Main loop — template method ───────────────────────

    def run(self) -> None:
        """Main entry point: idle ↔ running state machine.

        1. Calls :meth:`_setup` to initialise the store and UI state.
        2. Enters the idle loop (feature/steps editing).
        3. On user action transitions to running state.
        4. After running completes returns to idle.
        5. On Esc exits and resets the renderer.
        """
        self._setup()
        self.state = "idle"
        drain_keyboard()

        while True:
            if self.state == "idle":
                self.render_idle()
                key: bytes = read_key()
                handled: bool = self.handle_idle_key(key)
                if not handled:
                    # handle_idle_key returned False → exit requested
                    self.renderer.reset()
                    return

            elif self.state == "running":
                self._run_core_loop()
                # Running loop completed → back to idle
                # Clear buffered keys (especially the Enter/Esc that triggered
                # the stop) so they don't immediately re-trigger in idle.
                drain_keyboard()
                self.renderer.reset()
                if self.store is not None:
                    self.store.load()
                    self.store.load_all_templates()
                steps = self.get_steps()
                self.nav.n_items = 1 + self._count_nav_steps(steps) + 4
                self.state = "idle"

    # ── Idle rendering (three-area layout) ────────────────

    def render_idle(self, run_state: str = "idle") -> None:
        """Render the idle screen with three areas:

        **Area A** — Feature library (4 fixed slot rows).
        **Area B** — ▶ Start button.
        **Area C** — Execution path (editable step list, or locked
        runtime-status display when running).

        The *run_state* parameter controls the start button label/colour
        and whether the execution graph is locked:

        - ``"idle"``    → 绿色 "开始运行"，光标可移动，执行图可编辑
        - ``"running"`` → 红色 "停止运行"，光标固定在按钮上，执行图锁定
        - ``"waiting"`` → 黄色 "等待运行"，光标固定在按钮上，执行图锁定

        The layout adapts to the terminal width automatically.
        """
        if self.store is None:
            return

        steps: list[StepConfig] = self.get_steps()
        n_steps: int = len(steps)
        running: bool = run_state != "idle"

        # ── Build sample strings for width calculation ──
        # Collect actual tree prefixes (not a fixed sample) so deep
        # branches don't get truncated.  Running mode adds a "✓ " mark.
        btn_label: str = {"idle": "开始运行", "running": "停止运行",
                          "waiting": "等待运行"}.get(run_state, "开始运行")
        width_samples: list[str] = [btn_label]
        self._collect_width_samples(steps, "", width_samples, running=running)
        for slot in self.store:
            label = self.store.SLOT_LABELS.get(
                slot.feature_type, slot.feature_type
            )
            width_samples.append(
                f"{label} [截取] [删除]"
            )
        w: int = self._calc_width(width_samples, self.task_name, 42)

        lines: list[str] = [W.top_border(self.task_name, w)]
        lines.append(W.line("", w))

        # ── Area B: Start button ──
        # idle=绿色"开始运行"(光标可移动)
        # running=红色"停止运行"(光标固定)
        # waiting=黄色"等待运行"(光标固定)
        btn_color: str = {
            "idle": C.GREEN, "running": C.RED, "waiting": C.YELLOW,
        }.get(run_state, C.GREEN)
        btn_text: str = f"{btn_color}{C.BOLD}{btn_label}{C.RESET}"
        if running or self.nav.index == 0:
            lines.append(W.line_bg(btn_text, w))
        else:
            lines.append(W.line(btn_text, w))

        # ── Area C: Execution graph (tree with branches) ──
        lines.append(W.line("", w))
        lines.append(W.divider("执行图", w))
        lines.append(W.line("", w))

        if n_steps == 0:
            lines.append(W.line(f"{C.GRAY}  无步骤定义{C.RESET}", w))
        else:
            nav_idx: int = 1  # start button occupies index 0
            for i, step in enumerate(steps):
                is_last: bool = i == len(steps) - 1
                nav_idx = self._render_step_tree(
                    step, "", is_last, lines, w, nav_idx, running=running,
                )

        # ── Area A: Feature library (navigable) ──
        lines.append(W.line("", w))
        lines.append(W.divider("特征库", w))
        lines.append(W.line("", w))
        slot_start: int = 1 + self._count_nav_steps(steps)

        for i, slot in enumerate(self.store):
            nav_idx = slot_start + i
            label = self.store.SLOT_LABELS.get(
                slot.feature_type, slot.feature_type
            )
            cap_color: str = C.GREEN if slot.has_template() else C.YELLOW
            cap_b = f"[{C.BOLD}{C.WHITE}\u622a\u53d6{C.RESET}]"
            cap_g = f" {cap_color}\u622a\u53d6{C.RESET} "
            dlt_b = f"[{C.BOLD}{C.WHITE}\u5220\u9664{C.RESET}]"
            dlt_g = f" {C.RED}\u5220\u9664{C.RESET} "
            # 运行时光标固定在开始按钮，特征库行不会被选中
            if (not running) and self.nav.index == nav_idx:
                cap = cap_b if self._slot_action == 0 else cap_g
                dlt = dlt_b if self._slot_action == 1 else dlt_g
                row: str = f"{label} {cap}{dlt}"
                lines.append(W.line_sel(row, w))
            else:
                row = f"{label} {cap_g}{dlt_g}"
                lines.append(W.line(row, w))

        lines.append(W.line("", w))
        lines.append(W.bottom_border(w))

        self.renderer.render(lines)

    # ── Idle key handling ─────────────────────────────────

    def handle_idle_key(self, key: bytes) -> bool:
        """Process a single keypress in idle state.

        Args:
            key: Raw key bytes (K constant or raw scan code).

        Returns:
            ``True`` to continue the idle loop, ``False`` to exit.
        """
        if self.store is None:
            return False

        idx: int = self.nav.index
        steps: list[StepConfig] = self.get_steps()
        slot_start: int = 1 + self._count_nav_steps(steps)

        # ── ←→ / Shift+←→: adjust delay on step rows / toggle action on slot rows ──
        if key in (K.LEFT, K.RIGHT, K.SHIFT_LEFT, K.SHIFT_RIGHT):
            if idx >= 1 and idx < slot_start:
                # Step row: adjust delay directly (no Enter needed)
                # Shift+arrow = ±1000ms, regular arrow = ±10ms
                delta: int = 1000 if key in (K.SHIFT_LEFT, K.SHIFT_RIGHT) else 10
                sign: int = -1 if key in (K.LEFT, K.SHIFT_LEFT) else 1
                self._adjust_step_delay(idx - 1, sign * delta)
            elif idx >= slot_start and key in (K.LEFT, K.RIGHT):
                # Slot row: toggle 截取/删除 (Shift+arrow ignored on slots)
                self._slot_action = 1 if self._slot_action == 0 else 0
            return True

        # ── ↑↓: navigate ──
        if key in (K.UP, K.DOWN):
            old = idx
            self.nav.handle(key)
            if old != self.nav.index and self.nav.index >= slot_start:
                self._slot_action = 0
            return True

        # ── Enter: start / execute slot action ──
        if key == K.ENTER:
            if idx == 0 and self._can_start():
                self.state = "running"
            elif idx >= slot_start:
                if self._slot_action == 0:
                    self._capture_feature()
                else:
                    self._delete_feature()
            return True

        # ── Esc / Backspace: exit ──
        if key in (K.ESC, K.BS):
            return False

        return True

    # ── Internal helpers ──────────────────────────────────

    def _can_start(self) -> bool:
        """Return ``True`` if all 4 feature slots have templates."""
        if self.store is None:
            return False
        return all(slot.has_template() for slot in self.store)

    def _missing_feature_types(self) -> list[str]:
        """Return list of missing slot Chinese labels for UI hints."""
        if self.store is None:
            return []
        missing: list[str] = []
        for slot in self.store:
            if not slot.has_template():
                label = self.store.SLOT_LABELS.get(
                    slot.feature_type, slot.feature_type
                )
                missing.append(label)
        return missing

    def _adjust_step_delay(self, nav_idx: int, delta_ms: int) -> None:
        """Adjust the delay of the navigable step at *nav_idx* by *delta_ms*.

        Finds the :class:`StepConfig` at the given navigable index (using
        :meth:`_iter_nav_steps`), applies the delta (clamped to ≥ 0), and
        hot-saves the full step tree to config.

        Args:
            nav_idx: Zero-based index into the navigable steps list
                     (i.e. ``self.nav.index - 1`` since the start button
                     occupies navigator index 0).
            delta_ms: Milliseconds to add (positive) or subtract (negative).
        """
        steps: list[StepConfig] = self.get_steps()
        nav_steps: list[tuple[StepConfig, str]] = list(
            self._iter_nav_steps(steps)
        )
        if nav_idx < 0 or nav_idx >= len(nav_steps):
            return
        step, _nav_type = nav_steps[nav_idx]
        new_ms: int = max(0, int(step.delay * 1000) + delta_ms)
        step.delay = new_ms / 1000.0
        # Hot-save to config (serialise the full tree)
        if self.store is not None:
            steps_data: list[dict] = [self._step_to_dict(s) for s in steps]
            self.store.save_steps(steps_data)

    def _capture_feature(self) -> None:
        """Capture a feature for the currently selected slot.

        Uses the slot-based capture workflow (no type selection, no naming).
        """
        if self.store is None:
            return

        sel: int = self.nav.index
        steps: list[StepConfig] = self.get_steps()
        slot_start: int = 1 + self._count_nav_steps(steps)
        if sel < slot_start:
            # Start button selected — nothing to capture
            return
        slot_idx: int = sel - slot_start  # skip start + steps

        slots_list = list(self.store)
        if slot_idx < 0 or slot_idx >= len(slots_list):
            return
        slot = slots_list[slot_idx]

        # Lazy import to avoid circular dependency
        from tasks.auction.feature_editor import capture_slot_feature

        data_dir = getattr(self, "data_dir", None)
        if data_dir is None:
            return

        success: bool = capture_slot_feature(
            self.renderer, self.store, data_dir, slot.feature_type,
        )
        if success:
            self.store.save()
            self.store.load_all_templates()
        self.nav.n_items = 1 + self._count_nav_steps(steps) + 4
        self.renderer.reset()

    def _delete_feature(self) -> None:
        """Delete the currently selected slot's template."""
        if self.store is None:
            return

        sel: int = self.nav.index
        steps: list[StepConfig] = self.get_steps()
        slot_start: int = 1 + self._count_nav_steps(steps)
        if sel < slot_start:
            # Start button selected — nothing to delete
            return
        slot_idx: int = sel - slot_start  # skip start + steps

        slots_list = list(self.store)
        if slot_idx < 0 or slot_idx >= len(slots_list):
            return
        slot = slots_list[slot_idx]

        if not slot.has_template() and slot.template_file is None:
            return  # Nothing to delete

        self.store.clear_slot(slot.feature_type)
        self.store.save()
        self.store.load_all_templates()
        self.nav.n_items = 1 + self._count_nav_steps(steps) + 4
        self.renderer.reset()

    # ── Tree helpers (idle execution graph) ──────────────

    @staticmethod
    def _iter_steps(steps: list[StepConfig]):
        """Yield every :class:`StepConfig` in depth-first order.

        Visits each step, then recurses into ``children`` followed by
        each branch's ``steps``.  Used for serialisation and width
        calculation — includes **all** steps (navigable and non-navigable).
        """
        for s in steps:
            yield s
            if s.children:
                yield from BaseTask._iter_steps(s.children)
            if s.branches:
                for b in s.branches:
                    yield from BaseTask._iter_steps(b.steps)

    @classmethod
    def _iter_nav_steps(cls, steps: list[StepConfig]):
        """Yield ``(step, nav_type)`` for navigable steps only.

        Navigable steps are those whose delay value can be edited:
        - ``keypress`` / ``click`` → ``("delay")``  (delay shown on right)
        - ``wait`` → ``("wait")``  (the wait duration)
        - ``press`` / ``release`` → ``("delay")``  (future action types)

        ``match`` steps are **not** navigable (structural only).
        """
        for s in steps:
            if s.type in ("keypress", "click", "press", "release"):
                yield (s, "delay")
            elif s.type == "wait":
                yield (s, "wait")
            # match: not navigable
            if s.children:
                yield from cls._iter_nav_steps(s.children)
            if s.branches:
                for b in s.branches:
                    yield from cls._iter_nav_steps(b.steps)

    @classmethod
    def _count_nav_steps(cls, steps: list[StepConfig]) -> int:
        """Count navigable step nodes (keypress + wait, excluding match)."""
        return sum(1 for _ in cls._iter_nav_steps(steps))

    @classmethod
    def _collect_width_samples(
        cls,
        steps: list[StepConfig],
        prefix: str,
        samples: list[str],
        running: bool = False,
    ) -> None:
        """Collect display-width sample strings for panel width calculation.

        Traverses the step tree in the same depth-first order as
        :meth:`_render_step_tree`, accumulating actual branch prefixes
        (not a fixed sample) so deep branches are accounted for.

        When *running* is True, each sample includes a ``✓ `` mark prefix
        to match the runtime rendering (which shows status icons).

        ``match`` steps are not rendered as rows (they're structural only);
        their branches are promoted to the match step's own level so the
        condition labels appear as siblings of the surrounding steps.
        """
        mark: str = "\u2713 " if running else ""
        for i, step in enumerate(steps):
            is_last: bool = i == len(steps) - 1
            connector: str = "\u2514\u2500 " if is_last else "\u251c\u2500 "
            full_prefix: str = prefix + connector
            child_prefix: str = prefix + ("   " if is_last else "\u2502  ")

            if step.type in ("keypress", "click", "press", "release"):
                label: str = action_label(step.type)
                samples.append(
                    f"{full_prefix}{mark}等待 {int(step.delay * 1000)} ms  "
                    f"{label} {step.key.upper()}"
                )
            elif step.type == "wait":
                samples.append(
                    f"{full_prefix}{mark}等待 {int(step.delay * 1000)} ms"
                )
            elif step.type == "match":
                # match row is not rendered — skip sample
                pass
            else:
                samples.append(f"{full_prefix}{mark}{step.name}")

            for child in (step.children or []):
                cls._collect_width_samples([child], child_prefix, samples, running)

            for bi, branch in enumerate(step.branches or []):
                branch_last: bool = bi == len((step.branches or [])) - 1
                branch_connector: str = (
                    "\u2514\u2500 " if branch_last else "\u251c\u2500 "
                )
                # For match steps, branches render at the match's own level
                # (using ``prefix``) since the match row is skipped.
                branch_base: str = prefix if step.type == "match" else child_prefix
                branch_prefix: str = branch_base + branch_connector
                samples.append(f"{branch_prefix}{mark}{branch.condition}")
                branch_child_prefix: str = branch_base + (
                    "   " if branch_last else "\u2502  "
                )
                cls._collect_width_samples(
                    branch.steps, branch_child_prefix, samples, running,
                )

    def _render_step_tree(
        self,
        step: StepConfig,
        prefix: str,
        is_last: bool,
        lines: list[str],
        w: int,
        nav_idx: int,
        running: bool = False,
    ) -> int:
        """Render a step (and its children/branches) as tree rows.

        Rendering rules per step type:
        - ``keypress`` / ``click``: one row — ``等待 N ms  点击 KEY``
          (navigable, delay adjustable via ←→ / Shift+←→).
        - ``wait``: one row (navigable).
        - ``match``: **no row rendered** — structural only.  Its branches
          are promoted to the match step's own level so the condition
          labels (e.g. 有车状态 / 无车状态) appear as siblings of the
          surrounding steps, rendered in purple (MAUVE).

        Idle mode (*running* = False):
        - Labels plain, time numbers yellow, key names blue.
        - Selected rows use background highlight.

        Running mode (*running* = True):
        - Each row prefixed with a status icon (✓ / ▶ / · / ○).
        - ``runtime_status`` drives colours: done=normal, current=bold,
          dimmed=all-gray, waiting=normal.
        - No nav selection.

        Args:
            step: Current step node.
            prefix: Accumulated ancestor connectors (excludes this node's
                    own connector).
            is_last: Whether *step* is the last sibling on its level.
            lines: Output line list (appended in place).
            w: Panel width.
            nav_idx: Navigator index for the next navigable row.
            running: If True, render runtime-status display (locked).

        Returns:
            The next available navigator index after this subtree.
        """
        connector: str = "\u2514\u2500 " if is_last else "\u251c\u2500 "
        full_prefix: str = prefix + connector
        child_prefix: str = prefix + ("   " if is_last else "\u2502  ")

        status: str = step.runtime_status if running else ""

        if step.type in ("keypress", "click", "press", "release"):
            # ── Click row ──
            label: str = action_label(step.type)
            delay_ms: int = int(step.delay * 1000)
            is_sel: bool = (not running) and (self.nav.index == nav_idx)
            if running:
                content = self._runtime_click_content(status, label, delay_ms, step.key)
            else:
                content = (
                    f"等待 {C.YELLOW}{delay_ms}{C.RESET} ms  "
                    f"{label} {C.BLUE}{step.key.upper()}{C.RESET}"
                )
            mark: str = self._status_mark(status) if running else ""
            row_text: str = f"{mark} {content}" if running else content
            lines.append(W.tree_line(row_text, w, prefix=full_prefix, selected=is_sel))
            nav_idx += 1

        elif step.type == "wait":
            # ── Wait row ──
            delay_ms = int(step.delay * 1000)
            is_sel = (not running) and (self.nav.index == nav_idx)
            if running:
                content = self._runtime_wait_content(status, delay_ms)
            else:
                content = f"等待 {C.YELLOW}{delay_ms}{C.RESET} ms"
            mark = self._status_mark(status) if running else ""
            row_text = f"{mark} {content}" if running else content
            lines.append(W.tree_line(row_text, w, prefix=full_prefix, selected=is_sel))
            nav_idx += 1

        elif step.type == "match":
            # ── Match step: no row rendered (structural only) ──
            # Branches are rendered at the match's own level below.
            pass

        else:
            # Unknown type — render name as-is
            if running:
                content = self._runtime_plain_content(status, step.name)
            else:
                content = step.name
            mark = self._status_mark(status) if running else ""
            row_text = f"{mark} {content}" if running else content
            lines.append(W.tree_line(row_text, w, prefix=full_prefix))

        # ── Children and branches ──
        children: list[StepConfig] = step.children or []
        branches: list[Branch] = step.branches or []

        for ci, child in enumerate(children):
            child_last: bool = (ci == len(children) - 1) and not branches
            nav_idx = self._render_step_tree(
                child, child_prefix, child_last, lines, w, nav_idx, running,
            )

        # For match steps, branches render at the match's own level
        # (using ``prefix``) since the match row is skipped.  This makes
        # the condition labels appear as siblings of the surrounding steps.
        branch_base: str = prefix if step.type == "match" else child_prefix

        for bi, branch in enumerate(branches):
            branch_last: bool = bi == len(branches) - 1
            branch_connector: str = (
                "\u2514\u2500 " if branch_last else "\u251c\u2500 "
            )
            branch_prefix: str = branch_base + branch_connector
            # Branch condition row — purple (MAUVE) in both idle and running
            if running:
                b_status: str = branch.runtime_status
                b_content = self._runtime_branch_content(b_status, branch.condition)
                b_mark = self._status_mark(b_status)
                lines.append(W.tree_line(
                    f"{b_mark} {b_content}", w, prefix=branch_prefix,
                ))
            else:
                lines.append(
                    W.tree_line(
                        f"{C.MAUVE}{branch.condition}{C.RESET}",
                        w, prefix=branch_prefix,
                    )
                )
            # Steps inside this branch
            branch_child_prefix: str = branch_base + (
                "   " if branch_last else "\u2502  "
            )
            for si, sub_step in enumerate(branch.steps):
                sub_last: bool = si == len(branch.steps) - 1
                nav_idx = self._render_step_tree(
                    sub_step, branch_child_prefix, sub_last, lines, w, nav_idx, running,
                )

        return nav_idx

    # ── Runtime content builders (running mode) ──────────

    @staticmethod
    def _runtime_click_content(
        status: str, label: str, delay_ms: int, key_name: str | None,
    ) -> str:
        """Build the content string for a click row in running mode.

        - ``dimmed``: all gray.
        - other: labels plain, time yellow, key blue; ``current`` adds bold.
        """
        key_disp = (key_name or "").upper()
        if status == _ST_DIM:
            return f"{C.GRAY}等待 {delay_ms} ms  {label} {key_disp}{C.RESET}"
        bold = C.BOLD if status == _ST_CUR else ""
        return (
            f"{bold}等待 {C.YELLOW}{delay_ms}{C.RESET}{bold} ms  "
            f"{label} {C.BLUE}{key_disp}{C.RESET}"
        )

    @staticmethod
    def _runtime_wait_content(status: str, delay_ms: int) -> str:
        """Build the content string for a wait row in running mode."""
        if status == _ST_DIM:
            return f"{C.GRAY}等待 {delay_ms} ms{C.RESET}"
        bold = C.BOLD if status == _ST_CUR else ""
        return f"{bold}等待 {C.YELLOW}{delay_ms}{C.RESET}{bold} ms"

    @staticmethod
    def _runtime_branch_content(status: str, name: str) -> str:
        """Build the content string for a branch condition row in running mode.

        - ``dimmed``: gray (branch not taken).
        - other: purple (MAUVE); ``current`` adds bold.
        """
        if status == _ST_DIM:
            return f"{C.GRAY}{name}{C.RESET}"
        bold = C.BOLD if status == _ST_CUR else ""
        return f"{bold}{C.MAUVE}{name}{C.RESET}"

    @staticmethod
    def _runtime_plain_content(status: str, name: str) -> str:
        """Build the content string for a plain row in running mode."""
        if status == _ST_DIM:
            return f"{C.GRAY}{name}{C.RESET}"
        bold = C.BOLD if status == _ST_CUR else ""
        return f"{bold}{name}{C.RESET}"

    @staticmethod
    def _step_to_dict(step: StepConfig) -> dict:
        """Serialise a :class:`StepConfig` (and its subtree) to a dict.

        ``runtime_status`` is intentionally excluded — it is a
        transient field reset before each run.
        """
        d: dict = {
            "name": step.name,
            "type": step.type,
            "key": step.key,
            "delay": step.delay,
            "feature_type": step.feature_type,
            "node_id": step.node_id,
        }
        if step.children:
            d["children"] = [BaseTask._step_to_dict(c) for c in step.children]
        if step.branches:
            d["branches"] = [
                {
                    "condition": b.condition,
                    "loop": b.loop,
                    "node_id": b.node_id,
                    "steps": [BaseTask._step_to_dict(s) for s in b.steps],
                }
                for b in step.branches
            ]
        return d

    @staticmethod
    def _step_from_dict(d: dict) -> StepConfig:
        """Deserialise a :class:`StepConfig` (and its subtree) from a dict.

        Inverse of :meth:`_step_to_dict`.  Used to load saved step trees
        from the config file so that inline-edited delays persist across
        sessions.
        """
        step = StepConfig(
            name=d.get("name", ""),
            type=d.get("type", "keypress"),
            key=d.get("key"),
            delay=d.get("delay", 0.05),
            feature_type=d.get("feature_type"),
            node_id=d.get("node_id"),
        )
        if d.get("children"):
            step.children = [
                BaseTask._step_from_dict(c) for c in d["children"]
            ]
        if d.get("branches"):
            step.branches = [
                Branch(
                    condition=b["condition"],
                    steps=[
                        BaseTask._step_from_dict(s)
                        for s in b.get("steps", [])
                    ],
                    loop=b.get("loop"),
                    node_id=b.get("node_id"),
                )
                for b in d["branches"]
            ]
        return step

    @staticmethod
    def _reset_runtime_status(steps: list[StepConfig]) -> None:
        """Clear ``runtime_status`` on every node in the step tree.

        Called before each run iteration so the execution graph starts
        in a clean state.  Also clears :class:`Branch` nodes.
        """
        for s in steps:
            s.runtime_status = ""
            if s.children:
                BaseTask._reset_runtime_status(s.children)
            if s.branches:
                for b in s.branches:
                    b.runtime_status = ""
                    BaseTask._reset_runtime_status(b.steps)

    @staticmethod
    def _build_node_map(
        steps: list[StepConfig],
        mapping: dict[str, StepConfig | Branch],
    ) -> None:
        """Build a ``node_id → node`` lookup for runtime status updates.

        Walks the tree depth-first, registering every :class:`StepConfig`
        and :class:`Branch` that has a non-empty ``node_id``.
        """
        for s in steps:
            if s.node_id:
                mapping[s.node_id] = s
            if s.children:
                BaseTask._build_node_map(s.children, mapping)
            if s.branches:
                for b in s.branches:
                    if b.node_id:
                        mapping[b.node_id] = b
                    BaseTask._build_node_map(b.steps, mapping)

    @staticmethod
    def _status_mark(status: str) -> str:
        """Return the coloured status icon for a runtime status value.

        - ``done``    → green ✓
        - ``current`` → white bold ▶
        - ``dimmed``  → gray ·
        - other/empty → gray ○
        """
        if status == _ST_DONE:
            return f"{C.GREEN}\u2713{C.RESET}"
        if status == _ST_CUR:
            return f"{C.WHITE}{C.BOLD}\u25b6{C.RESET}"
        if status == _ST_DIM:
            return f"{C.GRAY}\u00b7{C.RESET}"  # ·
        return f"{C.GRAY}\u25cb{C.RESET}"  # ○

    @staticmethod
    def _calc_width(
        content_lines: list[str],
        title: str,
        min_w: int = 32,
    ) -> int:
        """Calculate the widest display width among content lines and title."""
        from udlrtui import display_width
        max_w: int = display_width(title) + 6
        for line in content_lines:
            max_w = max(max_w, display_width(line))
        return max(max_w + 6, min_w)

    # ── Focus guard integration ───────────────────────────

    def _make_guard(
        self,
        tree_w: int,
    ) -> FocusGuard:
        """Create a :class:`FocusGuard` with pause/resume callbacks.

        不再渲染单独的暂停界面，而是通过 ``_run_state`` 切换开始按钮
        的颜色和文字（黄色"等待运行" / 红色"停止运行"），保持 idle
        三区域布局不变。
        """

        def on_pause(title: str) -> None:
            self._run_state = "waiting"
            self.render_idle(run_state="waiting")

        def on_resume() -> None:
            self._run_state = "running"
            self.render_idle(run_state="running")

        return FocusGuard(on_pause=on_pause, on_resume=on_resume)
