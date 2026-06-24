"""Core task framework — abstract base class for all automation tasks.

Provides :class:`StepConfig` / :class:`Branch` dataclasses, :func:`calc_width`
utility, and the :class:`BaseTask` abstract base class using the template
method pattern for idle / running state management.

Layout (idle / running)::

    ╭── task_name ───────────────────────────────────────────╮
    │  菜单            │  执行图 / 特征库                      │
    │  ──────────────  │  ──────────────────────────────────   │
    │  › 开始运行      │   ├─ 等待 50 ms 点击 ENTER             │
    │  ┈┈ 设置 ┈┈      │   └─ 每隔 100 ms 检测 有车状态/无车状态 │
    │    执行图        │      ├─ 有车状态                       │
    │    特征库        │      │  └─ ...                         │
    │                  │      └─ 无车状态                       │
    ╰──────────────────────────────────────────────────────────╯

The left column is a fixed menu (``开始运行`` / ``执行图`` / ``特征库``,
separated by a ``设置`` divider).  The right column is always open and
shows either the execution graph or the feature library.  ``Tab``
switches column focus; ``Enter`` on a step row starts running *from
that row*.

Running is driven by :meth:`BaseTask._execute_tree`, a generic
tree-walking executor that handles ``keypress`` / ``hold`` / ``wait`` /
``match`` (branched or branchless) steps, dimming of non-taken branches,
focus-guard, 0 ms pause, and start-from-node skipping.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pyautogui

from udlrtui import C, K, Renderer, Navigator, widgets as W
from udlrtui import display_width, drain_keyboard, get_key, try_get_key

from core.focus import FocusGuard

if TYPE_CHECKING:
    from core.feature_store import FeatureSlot, FeatureStore


# ── StepConfig ────────────────────────────────────────────

@dataclass
class StepConfig:
    """Configuration for a single execution step.

    Attributes:
        name: Human-readable label (e.g. ``"Enter (搜索)"``).
        type: Step type — ``"keypress"`` | ``"hold"`` | ``"wait"`` |
            ``"match"`` | ``"click_match"``.
        key: :mod:`pyautogui` key name (only for ``"keypress"``,
            ``"hold"``, ``"press"``, ``"release"`` steps).
        delay: Seconds to wait after executing this step (for ``"hold"``,
            the hold duration).
        feature_type: Feature type string for ``"match"`` and
            ``"click_match"`` steps (e.g. ``"subaru_factory"``).
        button: Mouse button for ``"click_match"`` steps
            (``"left"`` | ``"right"`` | ``"middle"``).  Default ``"left"``.
        clicks: Number of clicks for ``"click_match"`` steps
            (1 = single, 2 = double).  Default 1.
        fallback_key: Optional key to press when a branched ``match``
            step matches no branch (e.g. ``"esc"`` to bail out and
            retry).  ``None`` = no fallback key.
        children: Optional ordered sub-steps executed after this step.
        branches: Optional conditional branches — each :class:`Branch`
            is taken when its condition matches the recognition result.
            Mutually exclusive with ``children`` in practice.
        node_id: Stable identifier for runtime status lookup (e.g.
            ``"enter_search"``).  Set by subclasses in :meth:`get_steps`.
        loop_until_match: For ``"match"`` steps with branches — when
            ``True``, loop {countdown, screenshot, branch-check} until
            a branch matches (instead of the default one-shot).  A
            branch may use ``loop="回到本步骤"`` to loop back to this
            match step after executing its sub-steps.
        runtime_status: Runtime-only status (``""`` / ``_ST_DONE`` /
            ``_ST_CUR`` / ``_ST_DIM``).  Reset before each run; not
            serialised to config.
        runtime_remaining_ms: Runtime-only countdown remaining
            milliseconds (only set during ``_ST_CUR``).  Not serialised.
    """

    name: str
    type: str = "keypress"
    key: str | None = None
    delay: float = 0.05
    feature_type: str | None = None
    button: str = "left"
    clicks: int = 1
    fallback_key: str | None = None
    children: list[StepConfig] | None = None
    branches: list[Branch] | None = None
    node_id: str | None = None
    loop_until_match: bool = False
    runtime_status: str = ""
    runtime_remaining_ms: int | None = None


@dataclass
class Branch:
    """A conditional branch in the execution tree.

    Attributes:
        condition: Human-readable condition label shown in the tree
            (e.g. ``"有车"``, ``"无车"``, ``"成功"``, ``"失败"``).
            Must match a value in :attr:`FeatureStore.SLOT_LABELS` so
            the executor can map it back to a feature type.
        steps: Ordered steps to execute when this branch is taken.
        loop: Loop-back label shown after the condition (e.g.
            ``"回到①"``, ``"结束"``, ``"回到本步骤"``).  ``"结束"``
            ends the run; ``"回到本步骤"`` (only meaningful on
            ``loop_until_match`` steps) loops back to the match step
            itself; any other value loops back to the top of the tree.
        node_id: Stable identifier for runtime status lookup.
        runtime_status: Runtime-only status (not serialised).
    """

    condition: str
    steps: list[StepConfig] = field(default_factory=list)
    loop: str | None = None
    node_id: str | None = None
    runtime_status: str = ""


# ── Action type label mapping ─────────────────────────────

_ACTION_LABELS: dict[str, str] = {
    "keypress": "点击",
    "click": "点击",
    "press": "按下",
    "release": "抬起",
    "hold": "按住",
    "wait": "等待",
    "match": "判断",
    "click_match": "点击",
}


def action_label(type_str: str) -> str:
    """Return the Chinese action label for a step type."""
    return _ACTION_LABELS.get(type_str, type_str)


def _fmt_ms(n: int) -> str:
    """Format milliseconds with thousands separator: 5000 -> "5,000"."""
    return f"{n:,}"


# ── Tree rendering constants ──────────────────────────────

_ST_DONE = "done"
_ST_CUR = "current"
_ST_DIM = "dimmed"  # branch not taken (greyed out)

# Left-column inner width (fits "停止运行" + pointer + padding).
_LEFT_W: int = 14


# ── Pause exception ───────────────────────────────────────

class _PauseExit(Exception):
    """用户在运行/暂停期间退出（Esc/Enter/失焦），用于跳出执行流程。"""


# ══════════════════════════════════════════════════════════
#  BaseTask — Abstract Base Class
# ══════════════════════════════════════════════════════════

class BaseTask(ABC):
    """Abstract base for all automation tasks.

    Implements the template-method pattern with a two-state
    (idle / running) main loop and a two-column "book-spread" layout.

    Subclasses provide:
      - :meth:`_setup` — initialisation
      - :meth:`get_steps` — step definitions
      - :meth:`_run_core_loop` — pre/post hooks around the generic
        executor (:meth:`_execute_tree`)

    Layout state:
      - ``left_nav`` — Navigator over the 3 left-menu items
        (开始运行 / 执行图 / 特征库).
      - ``right_nav`` — Navigator over the current right-panel content
        (execution steps or feature slots).
      - ``col_focus`` — ``"left"`` or ``"right"`` (``Tab`` to switch).
      - ``right_view`` — ``"steps"`` or ``"features"``.
    """

    task_name: str = ""
    task_tag: str = ""
    intro_text: str = ""

    # ── Abstract methods ──────────────────────────────────

    @abstractmethod
    def _setup(self) -> None:
        """Initialise the task (create store, load config, load templates).

        Subclasses **must** set ``self.store``, ``self.data_dir``,
        ``self._steps_cache`` and ``self.stats`` here.
        """
        ...

    @abstractmethod
    def get_steps(self) -> list[StepConfig]:
        """Return the execution step tree for this task."""
        ...

    @abstractmethod
    def _run_core_loop(self) -> None:
        """Running-state entry point.

        Default subclasses wrap :meth:`_execute_tree` with task-specific
        pre/post hooks (e.g. CapsLock toggle).  The generic executor
        handles the actual tree walk.
        """
        ...

    # ── Concrete initialisation ───────────────────────────

    def __init__(self, renderer: Renderer) -> None:
        """Initialise navigators to empty (overwritten by :meth:`_setup`)."""
        self.renderer: Renderer = renderer
        self.store: FeatureStore | None = None
        self.state: str = "idle"
        self.stats: dict = {"attempts": 0, "found": 0}
        self._guard: FocusGuard | None = None
        self._slot_action: int = 0
        self._run_state: str = "idle"
        # Two-column nav state
        self.left_nav: Navigator = Navigator(n_items=3)
        self.right_nav: Navigator = Navigator(n_items=1)
        self.col_focus: str = "left"
        self.right_view: str = "steps"
        # Edit mode for the right panel (steps: adjust delay; features:
        # toggle 截取/删除).  Enter to enter, Esc to exit.
        self.right_editing: bool = False
        # Run-from-row target (None = run from top).
        self._start_node: str | None = None
        # Transient message (e.g. missing-feature stop reason).
        self._missing_msg: str = ""
        # Viewport scroll offset for right panel (0 = top).
        self._viewport_start: int = 0
        # Executor internals (set in _execute_tree).
        self._node_map: dict[str, StepConfig | Branch] = {}
        self._skip_active: bool = False
        self._steps_cache: list[StepConfig] = []

    # ── Main loop — template method ───────────────────────

    def run(self) -> None:
        """Main entry point: idle ↔ running state machine."""
        self._setup()
        self._init_nav()
        self.state = "idle"
        drain_keyboard()

        while True:
            if self.state == "idle":
                self.render_idle()
                key: bytes = get_key()
                handled: bool = self.handle_idle_key(key)
                if not handled:
                    self.renderer.reset()
                    return

            elif self.state == "running":
                self._run_core_loop()
                # Running loop completed → back to idle.
                drain_keyboard()
                self.renderer.reset()
                if self.store is not None:
                    self.store.load()
                    self.store.load_all_templates()
                self._sync_right_nav()
                self._start_node = None
                self.right_editing = False
                # Keep _missing_msg so the first idle render shows it;
                # cleared on the next keypress in handle_idle_key.
                self.state = "idle"

    # ── Nav initialisation ────────────────────────────────

    def _init_nav(self) -> None:
        """(Re)initialise the two-column navigators after :meth:`_setup`."""
        self.left_nav = Navigator(n_items=3)
        self.left_nav.index = 0
        self.right_view = "steps"
        self.col_focus = "left"
        self.right_editing = False
        self._slot_action = 0
        self._start_node = None
        self._missing_msg = ""
        self._viewport_start = 0
        self._sync_right_nav()

    def _sync_right_nav(self) -> None:
        """Resize ``right_nav`` to match the current right-view content."""
        steps: list[StepConfig] = self.get_steps()
        if self.right_view == "steps":
            n: int = self._count_nav_steps(steps)
        else:
            n = len(self.store) if self.store is not None else 0
        n = max(n, 1)
        if self.right_nav is None:
            self.right_nav = Navigator(n_items=n)
        else:
            self.right_nav.n_items = n
            if self.right_nav.index >= n:
                self.right_nav.index = 0

    @staticmethod
    def _wrap_intro(text: str, max_width: int) -> list[str]:
        """Word-wrap *text* to fit within *max_width* display columns.

        Preserves explicit newlines.  CJK characters are counted as
        2 display columns; ASCII as 1.
        """
        def _dw(ch: str) -> int:
            o = ord(ch)
            return 2 if o > 0x7F else 1

        lines: list[str] = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            cur: str = ""
            cur_w: int = 0
            for ch in paragraph:
                cw: int = _dw(ch)
                if cur_w + cw > max_width:
                    lines.append(cur)
                    cur = ch
                    cur_w = cw
                else:
                    cur += ch
                    cur_w += cw
            if cur:
                lines.append(cur)
        return lines

    # ── Idle rendering (two-column book spread) ───────────

    def render_idle(self, run_state: str = "idle") -> None:
        """Render the two-column idle / running screen.

        Left column: ``开始运行`` / ``设置`` divider / ``执行图`` /
        ``特征库``.  Right column: always open; shows the execution
        graph (default) or the feature library.  During running the
        right column is forced to the execution graph with live
        runtime status, and the left menu is locked.

        When the right panel content exceeds the terminal height,
        a viewport window is applied: the selected row stays visible
        by sliding the viewport as the user navigates.  The TUI
        frame height is clamped to the terminal height.
        """
        import shutil

        if self.store is None:
            return

        steps: list[StepConfig] = self.get_steps()
        running: bool = run_state != "idle"

        view: str = "steps" if running else self.right_view

        left_rows: list[tuple[str, bool]] = self._build_left_menu(run_state)

        if view == "steps":
            right_rows, right_w = self._build_steps_panel(steps, running)
            right_title = "执行图"
        else:
            right_rows, right_w = self._build_features_panel(running)
            right_title = "特征库"

        # Missing-feature stop message (shown until next keypress).
        if self._missing_msg:
            right_rows.append((
                W.inner_line(
                    f"{C.RED}缺少特征: {self._missing_msg}{C.RESET}", right_w,
                ),
                False,
            ))

        # ── Viewport: clamp to terminal height ──
        term_h: int = shutil.get_terminal_size().lines
        # Outer frame: top border + blank + header + separator + bottom border = 5
        max_content: int = max(4, term_h - 5)
        total: int = len(right_rows)

        if total > max_content:
            # Find the selected (highlighted) row index.
            sel_idx: int = 0
            for i, (_inner, is_sel) in enumerate(right_rows):
                if is_sel:
                    sel_idx = i
                    break

            # Ensure sel_idx is inside the current viewport.
            vs: int = self._viewport_start
            if sel_idx < vs:
                vs = sel_idx
            elif sel_idx >= vs + max_content:
                vs = sel_idx - max_content + 1
            vs = max(0, min(vs, total - max_content))
            self._viewport_start = vs

            # Slice rows.
            visible: list[tuple[str, bool]] = right_rows[vs:vs + max_content]
            right_rows = visible

        lines: list[str] = W.two_column_frame(
            self.task_name, "菜单", left_rows,
            right_title, right_rows, _LEFT_W, right_w,
        )
        self.renderer.render(lines)

    def _build_left_menu(
        self, run_state: str,
    ) -> list[tuple[str, bool]]:
        """Build the left-column menu rows (inner strings)."""
        running: bool = run_state != "idle"
        rows: list[tuple[str, bool]] = []

        # Item 0: 开始运行 / 停止运行
        btn_label: str = "停止运行" if running else "开始运行"
        btn_color: str = C.RED if running else C.GREEN
        sel0: bool = running or (
            self.col_focus == "left" and self.left_nav.index == 0
        )
        btn_content: str = f"{btn_color}{C.BOLD}{btn_label}{C.RESET}"
        rows.append((
            W.inner_sel(btn_content, _LEFT_W) if sel0
            else W.inner_line(f"{btn_color}{btn_label}{C.RESET}", _LEFT_W),
            sel0,
        ))

        # 设置 divider (non-selectable section header)
        rows.append((W.inner_divider("设置", _LEFT_W), False, True))

        # Item 1: 执行图 — underline marks the currently-opened view
        sel1: bool = (not running) and self.col_focus == "left" \
            and self.left_nav.index == 1
        if running:
            label1: str = f"{C.GRAY}执行图{C.RESET}"
        elif sel1:
            label1 = "执行图"
        elif self.right_view == "steps":
            label1 = f"{C.UNDERLINE}执行图{C.RESET}"
        else:
            label1 = "执行图"
        rows.append((
            W.inner_sel(label1, _LEFT_W) if sel1
            else W.inner_line(label1, _LEFT_W),
            sel1,
        ))

        # Item 2: 特征库 — underline marks the currently-opened view
        sel2: bool = (not running) and self.col_focus == "left" \
            and self.left_nav.index == 2
        if running:
            label2: str = f"{C.GRAY}特征库{C.RESET}"
        elif sel2:
            label2 = "特征库"
        elif self.right_view == "features":
            label2 = f"{C.UNDERLINE}特征库{C.RESET}"
        else:
            label2 = "特征库"
        rows.append((
            W.inner_sel(label2, _LEFT_W) if sel2
            else W.inner_line(label2, _LEFT_W),
            sel2,
        ))

        # ── 介绍文本 (idle only, below 特征库) ──
        if not running and self.intro_text:
            inner_w: int = _LEFT_W - 2  # 1-char padding each side
            wrapped: list[str] = self._wrap_intro(self.intro_text, inner_w)
            # Gap line above intro.
            rows.append((W.inner_line("", _LEFT_W), False))
            for line in wrapped:
                rows.append((
                    W.inner_line(
                        f"{C.GRAY}{C.DIM}{line}{C.RESET}", _LEFT_W,
                    ),
                    False,
                ))

        return rows

    def _build_steps_panel(
        self, steps: list[StepConfig], running: bool,
    ) -> tuple[list[tuple[str, bool]], int]:
        """Build the execution-graph right-column rows + inner width."""
        samples: list[str] = []
        self._collect_width_samples(steps, "", samples, running=running)
        max_w: int = max([display_width(s) for s in samples] + [20])
        right_w: int = max_w + 4  # pointer(2) + padding(2)

        rows: list[tuple[str, bool]] = []
        if not steps:
            rows.append((
                W.inner_line(f"{C.GRAY}  无步骤定义{C.RESET}", right_w), False,
            ))
            return rows, right_w

        nav_idx: int = 0  # 0-based into navigable steps
        for i, step in enumerate(steps):
            is_last: bool = i == len(steps) - 1
            nav_idx = self._render_step_inner(
                step, "", is_last, rows, right_w, nav_idx, running,
            )
        return rows, right_w

    def _build_features_panel(
        self, running: bool,
    ) -> tuple[list[tuple[str, bool]], int]:
        """Build the feature-library right-column rows + inner width."""
        slots = list(self.store) if self.store is not None else []
        samples: list[str] = []
        for slot in slots:
            label = self.store.SLOT_LABELS.get(
                slot.feature_type, slot.feature_type,
            )
            samples.append(f"{label} [截取] [删除]")
        max_w: int = max([display_width(s) for s in samples] + [20])
        right_w: int = max_w + 4

        rows: list[tuple[str, bool]] = []
        for i, slot in enumerate(slots):
            label = self.store.SLOT_LABELS.get(
                slot.feature_type, slot.feature_type,
            )
            cap_color: str = C.GREEN if slot.has_template() else C.YELLOW
            cap_b = f"[{C.BOLD}{cap_color}截取{C.RESET}]"
            cap_g = f" {cap_color}截取{C.RESET} "
            dlt_b = f"[{C.BOLD}{C.RED}删除{C.RESET}]"
            dlt_g = f" {C.RED}删除{C.RESET} "
            is_cursor: bool = (not running) and self.col_focus == "right" \
                and self.right_nav.index == i
            if is_cursor:
                # Cursor on this row — brackets on the current action
                cap = cap_b if self._slot_action == 0 else cap_g
                dlt = dlt_b if self._slot_action == 1 else dlt_g
                content: str = f"{label} {cap}{dlt}"
                rows.append((W.inner_sel(content, right_w), True))
            else:
                content = f"{label} {cap_g}{dlt_g}"
                rows.append((W.inner_line(content, right_w), False))
        return rows, right_w

    # ── Idle key handling ─────────────────────────────────

    def handle_idle_key(self, key: bytes) -> bool:
        """Process a single keypress in idle state.

        Column switching: Enter on ``执行图`` / ``特征库`` in the left
        column opens that view and moves focus to the right panel.  Esc
        in the right panel moves focus back to the left menu.

        Edit mode: in the right panel, ``Enter`` enters edit mode for
        the focused row (steps: delay adjustment; features: action
        toggle).  In edit mode ``←→`` adjusts the value instead of
        switching columns.  ``Esc`` exits edit mode; a second ``Enter``
        on a step runs from that row.
        """
        if self.store is None:
            return False

        # Dismiss the missing-feature message on the first keypress.
        if self._missing_msg:
            self._missing_msg = ""

        # Esc / Backspace:
        # - Right panel → back to left menu
        # - Left menu → exit task
        if key in (K.ESC, K.BS):
            if self.col_focus == "right":
                self.col_focus = "left"
                return True
            return False

        if self.col_focus == "left":
            return self._handle_left_key(key)
        return self._handle_right_key(key)

    def _handle_left_key(self, key: bytes) -> bool:
        """Handle keys when the left menu is focused.

        Up/Down moves the cursor through the left menu **without**
        changing the right-panel view — the view only changes on Enter.
        Enter on ``开始运行`` starts a run; Enter on ``执行图`` /
        ``特征库`` opens that content in the right panel and moves
        focus there.
        """
        if key in (K.UP, K.DOWN):
            self.left_nav.handle(key)
            return True

        if key == K.ENTER:
            if self.left_nav.index == 0:
                # 开始运行 → run from top
                if self._can_start():
                    from core.focus import activate_game_window
                    if activate_game_window():
                        self._start_node = None
                        self.state = "running"
            elif self.left_nav.index == 1:
                # 执行图 → open in right panel and move focus there
                self.right_view = "steps"
                self._viewport_start = 0
                self._sync_right_nav()
                self.col_focus = "right"
            elif self.left_nav.index == 2:
                # 特征库 → open in right panel and move focus there
                self.right_view = "features"
                self._viewport_start = 0
                self._sync_right_nav()
                self.col_focus = "right"
            return True

        return True

    def _handle_right_key(self, key: bytes) -> bool:
        """Handle keys when the right panel is focused.

        **Steps view**:
        - Up/Down: navigate rows
        - ``←→``: adjust delay (+-10 ms; Shift = +-1,000 ms)
        - Enter: run from this row
        - Esc: back to left menu

        **Features view**:
        - Up/Down: navigate slots
        - ``←→``: toggle slot action (截取 / 删除)
        - Enter: execute selected action
        - Esc: back to left menu
        """
        steps: list[StepConfig] = self.get_steps()

        if self.right_view == "steps":
            # ── Steps: direct navigation + adjustment ──
            if key in (K.UP, K.DOWN):
                self.right_nav.handle(key)
                return True
            if key in (K.LEFT, K.RIGHT, K.SHIFT_LEFT, K.SHIFT_RIGHT):
                delta: int = 1000 if key in (K.SHIFT_LEFT, K.SHIFT_RIGHT) else 10
                sign: int = -1 if key in (K.LEFT, K.SHIFT_LEFT) else 1
                self._adjust_step_delay(
                    self.right_nav.index, sign * delta,
                )
                return True
            if key == K.ENTER:
                # Run from this row
                nav_steps = list(self._iter_nav_steps(steps))
                if 0 <= self.right_nav.index < len(nav_steps):
                    step, _t = nav_steps[self.right_nav.index]
                    if self._can_start() and step.node_id:
                        from core.focus import activate_game_window
                        if activate_game_window():
                            self._start_node = step.node_id
                            self.state = "running"
                return True
            return True

        # ── Features view ──
        if key in (K.UP, K.DOWN):
            old = self.right_nav.index
            self.right_nav.handle(key)
            if old != self.right_nav.index:
                self._slot_action = 0
            return True
        if key in (K.LEFT, K.RIGHT):
            self._slot_action = 1 if self._slot_action == 0 else 0
            return True
        if key == K.ENTER:
            if self._slot_action == 0:
                self._capture_feature()
            else:
                self._delete_feature()
            return True
        return True

    # ── Internal helpers ──────────────────────────────────

    def _can_start(self) -> bool:
        """Return ``True`` if a run can start.

        Missing feature templates do **not** block start — the run will
        stop at the first ``match`` step whose feature is missing (see
        :meth:`_execute_tree`).  Only the store must exist.
        """
        return self.store is not None

    def _adjust_step_delay(self, nav_idx: int, delta_ms: int) -> None:
        """Adjust the delay of the navigable node at *nav_idx* by *delta_ms*.

        *nav_idx* is 0-based into the navigable steps list (i.e.
        ``right_nav.index`` in the steps view).
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
        if self.store is not None:
            steps_data: list[dict] = [self._step_to_dict(s) for s in steps]
            self.store.save_steps(steps_data)

    def _capture_feature(self) -> None:
        """Capture a feature for the currently selected slot."""
        if self.store is None:
            return
        slot_idx: int = self.right_nav.index
        slots_list = list(self.store)
        if slot_idx < 0 or slot_idx >= len(slots_list):
            return
        slot = slots_list[slot_idx]

        from core.feature_editor import capture_slot_feature

        data_dir = getattr(self, "data_dir", None)
        if data_dir is None:
            return

        success: bool = capture_slot_feature(
            self.renderer, self.store, data_dir, slot.feature_type,
        )
        if success:
            self.store.save()
            self.store.load_all_templates()
        self._sync_right_nav()
        self.renderer.reset()

    def _delete_feature(self) -> None:
        """Delete the currently selected slot's template."""
        if self.store is None:
            return
        slot_idx: int = self.right_nav.index
        slots_list = list(self.store)
        if slot_idx < 0 or slot_idx >= len(slots_list):
            return
        slot = slots_list[slot_idx]

        if not slot.has_template() and slot.template_file is None:
            return

        self.store.clear_slot(slot.feature_type)
        self.store.save()
        self.store.load_all_templates()
        self._sync_right_nav()
        self.renderer.reset()

    # ── Tree helpers (execution graph) ────────────────────

    @staticmethod
    def _iter_steps(steps: list[StepConfig]):
        """Yield every :class:`StepConfig` in depth-first order."""
        for s in steps:
            yield s
            if s.children:
                yield from BaseTask._iter_steps(s.children)
            if s.branches:
                for b in s.branches:
                    yield from BaseTask._iter_steps(b.steps)

    @classmethod
    def _iter_nav_steps(cls, steps: list[StepConfig]):
        """Yield ``(node, nav_type)`` for navigable nodes only."""
        for s in steps:
            if s.type in ("keypress", "click", "hold", "press", "release"):
                yield (s, "delay")
            elif s.type == "wait":
                yield (s, "wait")
            elif s.type == "match":
                yield (s, "match_delay")
            elif s.type == "click_match":
                yield (s, "click_match")
            if s.children:
                yield from cls._iter_nav_steps(s.children)
            if s.branches:
                for b in s.branches:
                    yield from cls._iter_nav_steps(b.steps)

    @classmethod
    def _count_nav_steps(cls, steps: list[StepConfig]) -> int:
        """Count navigable nodes."""
        return sum(1 for _ in cls._iter_nav_steps(steps))

    def _match_feature_label(self, step: StepConfig) -> str:
        """Resolve a match step's feature name via ``SLOT_LABELS``."""
        if not step.feature_type or self.store is None:
            return step.name
        parts: list[str] = step.feature_type.split("/")
        labels: list[str] = [
            self.store.SLOT_LABELS.get(p, p) for p in parts
        ]
        return "/".join(labels)

    def _collect_width_samples(
        self,
        steps: list[StepConfig],
        prefix: str,
        samples: list[str],
        running: bool = False,
    ) -> None:
        """Collect plain-text sample strings for right-column width calc."""
        for i, step in enumerate(steps):
            is_last: bool = i == len(steps) - 1
            connector: str = "\u2514\u2500 " if is_last else "\u251c\u2500 "
            full_prefix: str = prefix + connector
            child_prefix: str = prefix + ("    " if is_last else "\u2502   ")

            if step.type in ("keypress", "click", "press", "release", "hold"):
                label: str = action_label(step.type)
                samples.append(
                    f"000 {full_prefix}等待 {_fmt_ms(int(step.delay * 1000))} ms "
                    f"{label} {(step.key or '').upper()}"
                )
            elif step.type == "wait":
                samples.append(
                    f"000 {full_prefix}等待 {_fmt_ms(int(step.delay * 1000))} ms"
                )
            elif step.type == "match":
                feat_label: str = self._match_feature_label(step)
                samples.append(
                    f"000 {full_prefix}每隔 {_fmt_ms(int(step.delay * 1000))} ms "
                    f"检测 {feat_label}"
                )
            elif step.type == "click_match":
                feat_label = self._match_feature_label(step)
                samples.append(
                    f"000 {full_prefix}每隔 {_fmt_ms(int(step.delay * 1000))} ms "
                    f"点击 {feat_label}"
                )
            else:
                samples.append(f"    {full_prefix}{step.name}")

            for child in (step.children or []):
                self._collect_width_samples(
                    [child], child_prefix, samples, running,
                )

            for bi, branch in enumerate(step.branches or []):
                branch_last: bool = bi == len((step.branches or [])) - 1
                branch_connector: str = (
                    "\u2514\u2500 " if branch_last else "\u251c\u2500 "
                )
                branch_base: str = child_prefix
                branch_prefix: str = branch_base + branch_connector
                samples.append(f"    {branch_prefix}{branch.condition}")
                branch_child_prefix: str = branch_base + (
                    "    " if branch_last else "\u2502   "
                )
                self._collect_width_samples(
                    branch.steps, branch_child_prefix, samples, running,
                )

    def _render_step_inner(
        self,
        step: StepConfig,
        prefix: str,
        is_last: bool,
        rows: list[tuple[str, bool]],
        right_w: int,
        nav_idx: int,
        running: bool = False,
    ) -> int:
        """Render a step (and its children/branches) as inner rows.

        Like :meth:`_render_step_tree` but appends ``(inner_str, selected)``
        tuples for the two-column composer instead of full box lines.
        Returns the next available navigator index.
        """
        connector: str = "\u2514\u2500 " if is_last else "\u251c\u2500 "
        full_prefix: str = prefix + connector
        # Child indent uses 4-char spacers to match the line-number column.
        child_prefix: str = prefix + ("    " if is_last else "\u2502   ")

        status: str = step.runtime_status if running else ""

        if step.type in ("keypress", "click", "press", "release", "hold"):
            label: str = action_label(step.type)
            delay_ms: int = int(step.delay * 1000)
            is_sel: bool = (
                (not running) and self.col_focus == "right"
                and self.right_nav.index == nav_idx
            ) or (running and status == _ST_CUR)
            if running:
                display_ms = (
                    step.runtime_remaining_ms if status == _ST_CUR
                    and step.runtime_remaining_ms is not None else delay_ms
                )
                content = self._runtime_click_content(
                    status, label, display_ms, step.key,
                )
            else:
                # Underline the delay when this row is being edited.
                is_edit: bool = is_sel and self.right_editing
                ms_str: str = (
                    f"{C.UNDERLINE}{C.BOLD}{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                    if is_edit else f"{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                )
                content = (
                    f"等待 {ms_str} ms "
                    f"{label} {C.BLUE}{(step.key or '').upper()}{C.RESET}"
                )
            rows.append((
                W.inner_tree(
                    content, right_w,
                    prefix=f"{nav_idx + 1:>3} {full_prefix}",
                    selected=is_sel,
                ),
                is_sel,
            ))
            nav_idx += 1

        elif step.type == "wait":
            delay_ms = int(step.delay * 1000)
            is_sel = (
                (not running) and self.col_focus == "right"
                and self.right_nav.index == nav_idx
            ) or (running and status == _ST_CUR)
            if running:
                display_ms = (
                    step.runtime_remaining_ms if status == _ST_CUR
                    and step.runtime_remaining_ms is not None else delay_ms
                )
                content = self._runtime_wait_content(status, display_ms)
            else:
                is_edit = is_sel and self.right_editing
                ms_str = (
                    f"{C.UNDERLINE}{C.BOLD}{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                    if is_edit else f"{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                )
                content = f"等待 {ms_str} ms"
            rows.append((
                W.inner_tree(
                    content, right_w,
                    prefix=f"{nav_idx + 1:>3} {full_prefix}",
                    selected=is_sel,
                ),
                is_sel,
            ))
            nav_idx += 1

        elif step.type == "click_match":
            delay_ms = int(step.delay * 1000)
            feat_label: str = self._match_feature_label(step)
            is_sel = (
                (not running) and self.col_focus == "right"
                and self.right_nav.index == nav_idx
            ) or (running and status == _ST_CUR)
            if running:
                display_ms = (
                    step.runtime_remaining_ms if status == _ST_CUR
                    and step.runtime_remaining_ms is not None else delay_ms
                )
                content = self._runtime_match_content(
                    status, display_ms, feat_label,
                )
            else:
                is_edit = is_sel and self.right_editing
                ms_str = (
                    f"{C.UNDERLINE}{C.BOLD}{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                    if is_edit else f"{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                )
                content = (
                    f"每隔 {ms_str} ms "
                    f"点击 {feat_label}"
                )
            rows.append((
                W.inner_tree(
                    content, right_w,
                    prefix=f"{nav_idx + 1:>3} {full_prefix}",
                    selected=is_sel,
                ),
                is_sel,
            ))
            nav_idx += 1

        elif step.type == "match":
            delay_ms = int(step.delay * 1000)
            feat_label: str = self._match_feature_label(step)
            is_sel = (
                (not running) and self.col_focus == "right"
                and self.right_nav.index == nav_idx
            ) or (running and status == _ST_CUR)
            if running:
                display_ms = (
                    step.runtime_remaining_ms if status == _ST_CUR
                    and step.runtime_remaining_ms is not None else delay_ms
                )
                content = self._runtime_match_content(
                    status, display_ms, feat_label,
                )
            else:
                is_edit = is_sel and self.right_editing
                ms_str = (
                    f"{C.UNDERLINE}{C.BOLD}{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                    if is_edit else f"{C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET}"
                )
                content = (
                    f"每隔 {ms_str} ms "
                    f"检测 {feat_label}"
                )
            rows.append((
                W.inner_tree(
                    content, right_w,
                    prefix=f"{nav_idx + 1:>3} {full_prefix}",
                    selected=is_sel,
                ),
                is_sel,
            ))
            nav_idx += 1

        else:
            content = step.name
            is_sel = running and status == _ST_CUR
            rows.append((
                W.inner_tree(
                    content, right_w,
                    prefix=f"    {full_prefix}",
                    selected=is_sel,
                ),
                is_sel,
            ))

        # ── Children and branches ──
        children: list[StepConfig] = step.children or []
        branches: list[Branch] = step.branches or []

        for ci, child in enumerate(children):
            child_last: bool = (ci == len(children) - 1) and not branches
            nav_idx = self._render_step_inner(
                child, child_prefix, child_last, rows, right_w, nav_idx, running,
            )

        branch_base: str = child_prefix

        for bi, branch in enumerate(branches):
            branch_last: bool = bi == len(branches) - 1
            branch_connector: str = (
                "\u2514\u2500 " if branch_last else "\u251c\u2500 "
            )
            branch_prefix: str = branch_base + branch_connector
            if running:
                b_status: str = branch.runtime_status
                b_content = self._runtime_branch_content(b_status, branch.condition)
                b_sel: bool = b_status == _ST_CUR
            else:
                b_content = f"{C.MAUVE}{branch.condition}{C.RESET}"
                b_sel = False
            rows.append((
                W.inner_tree(b_content, right_w, prefix=branch_prefix, selected=b_sel),
                b_sel,
            ))
            branch_child_prefix: str = branch_base + (
                "   " if branch_last else "\u2502  "
            )
            for si, sub_step in enumerate(branch.steps):
                sub_last: bool = si == len(branch.steps) - 1
                nav_idx = self._render_step_inner(
                    sub_step, branch_child_prefix, sub_last, rows, right_w,
                    nav_idx, running,
                )

        return nav_idx

    # ── Runtime content builders (running mode) ──────────

    @staticmethod
    def _runtime_click_content(
        status: str, label: str, delay_ms: int, key_name: str | None,
    ) -> str:
        key_disp = (key_name or "").upper()
        if status == _ST_DIM:
            return f"{C.GRAY}等待 {_fmt_ms(delay_ms)} ms {label} {key_disp}{C.RESET}"
        return (
            f"等待 {C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET} ms "
            f"{label} {C.BLUE}{key_disp}{C.RESET}"
        )

    @staticmethod
    def _runtime_wait_content(status: str, delay_ms: int) -> str:
        if status == _ST_DIM:
            return f"{C.GRAY}等待 {_fmt_ms(delay_ms)} ms{C.RESET}"
        return f"等待 {C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET} ms"

    @staticmethod
    def _runtime_match_content(
        status: str, delay_ms: int, feature_label: str,
    ) -> str:
        if status == _ST_DIM:
            return f"{C.GRAY}每隔 {_fmt_ms(delay_ms)} ms 检测 {feature_label}{C.RESET}"
        return f"每隔 {C.YELLOW}{_fmt_ms(delay_ms)}{C.RESET} ms 检测 {feature_label}"

    @staticmethod
    def _runtime_branch_content(status: str, name: str) -> str:
        if status == _ST_DIM:
            return f"{C.GRAY}{name}{C.RESET}"
        return f"{C.MAUVE}{name}{C.RESET}"

    # ── Serialisation ─────────────────────────────────────

    @staticmethod
    def _step_to_dict(step: StepConfig) -> dict:
        """Serialise a :class:`StepConfig` (and its subtree) to a dict."""
        d: dict = {
            "name": step.name,
            "type": step.type,
            "key": step.key,
            "delay": step.delay,
            "feature_type": step.feature_type,
            "button": step.button,
            "clicks": step.clicks,
            "fallback_key": step.fallback_key,
            "node_id": step.node_id,
            "loop_until_match": step.loop_until_match,
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
        """Deserialise a :class:`StepConfig` from a dict."""
        step = StepConfig(
            name=d.get("name", ""),
            type=d.get("type", "keypress"),
            key=d.get("key"),
            delay=d.get("delay", 0.05),
            feature_type=d.get("feature_type"),
            button=d.get("button", "left"),
            clicks=d.get("clicks", 1),
            fallback_key=d.get("fallback_key"),
            node_id=d.get("node_id"),
            loop_until_match=d.get("loop_until_match", False),
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
        """Clear ``runtime_status`` on every node in the step tree."""
        for s in steps:
            s.runtime_status = ""
            s.runtime_remaining_ms = None
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
        """Build a ``node_id → node`` lookup for runtime status updates."""
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

    @classmethod
    def _subtree_node_ids(cls, steps: list[StepConfig]) -> list[str]:
        """Collect every ``node_id`` in a subtree (depth-first)."""
        ids: list[str] = []
        for s in steps:
            if s.node_id:
                ids.append(s.node_id)
            if s.children:
                ids.extend(cls._subtree_node_ids(s.children))
            if s.branches:
                for b in s.branches:
                    if b.node_id:
                        ids.append(b.node_id)
                    ids.extend(cls._subtree_node_ids(b.steps))
        return ids

    # ── Generic tree executor ─────────────────────────────

    def _make_guard(self) -> FocusGuard:
        """Create a :class:`FocusGuard` (失焦即终止运行)."""
        return FocusGuard()

    def _feature_ready(self, feature_type: str) -> bool:
        """Return ``True`` if *feature_type*'s slot has a template + region."""
        if self.store is None:
            return False
        slot = self.store.get_slot(feature_type)
        return (
            slot is not None
            and slot.has_template()
            and slot.region is not None
        )

    def _branch_feature_type(self, branch: Branch) -> str | None:
        """Map a branch's condition label back to a feature type.

        Uses the reverse of :attr:`FeatureStore.SLOT_LABELS`.
        """
        if self.store is None:
            return None
        rev = {v: k for k, v in self.store.SLOT_LABELS.items()}
        return rev.get(branch.condition)

    def _execute_tree(self, start_node_id: str | None = None) -> None:
        """Generic tree-walking executor — drives the whole run.

        Walks :meth:`get_steps` in execution order, handling
        ``keypress`` / ``hold`` / ``wait`` / ``match`` steps:

        - ``match`` **with branches**: countdown, screenshot, take the
          branch whose feature matches; if none matches, press
          ``fallback_key`` (if any) and loop.
        - ``match`` **without branches**: loop {countdown, screenshot}
          until the feature is detected, then continue.

        ``branch.loop == "结束"`` ends the run (and increments
        ``stats["found"]``); any other value loops back to the top.

        When *start_node_id* is set, the first pass skips nodes before
        it (descending into the branch that contains it); subsequent
        passes run from the top.

        Stops (returns) on: user interrupt (Esc/Enter), focus loss,
        missing feature at a ``match`` step, or an ``"结束"`` branch.
        """
        if self.store is None:
            return

        drain_keyboard()
        self._run_state = "running"
        self._start_node = start_node_id
        self._missing_msg = ""

        steps: list[StepConfig] = self.get_steps()
        self._node_map = {}
        self._build_node_map(steps, self._node_map)

        guard: FocusGuard = self._make_guard()
        self._guard = guard

        # Wait for the game window to be fully in the foreground.
        _focus_ok: bool = False
        for _ in range(10):
            if guard.check():
                _focus_ok = True
                break
            time.sleep(0.05)
        if not _focus_ok:
            return

        self._skip_active = start_node_id is not None

        try:
            while True:
                key = try_get_key()
                if key is not None and key in (K.ESC, K.BS, K.ENTER):
                    return
                if not guard.check():
                    return

                self.stats["attempts"] += 1
                self._reset_runtime_status(steps)

                sig: str = self._walk(steps)
                if sig in ("end", "stop"):
                    return
                # "continue" → loop again from the top; first pass done.
                self._skip_active = False
                self._start_node = None
        except _PauseExit:
            return

    def _walk(self, steps: list[StepConfig]) -> str:
        """Walk a step list; return ``"continue"`` | ``"end"`` | ``"stop"``."""
        for step in steps:
            if self._skip_active:
                if step.node_id == self._start_node:
                    self._skip_active = False
                    sig = self._exec_step(step)
                    if sig != "continue":
                        return sig
                elif self._start_in_subtree(step):
                    sig = self._descend_skipped(step)
                    if sig != "continue":
                        return sig
                # else: skip entirely
                continue
            sig = self._exec_step(step)
            if sig != "continue":
                return sig
        return "continue"

    def _descend_skipped(self, step: StepConfig) -> str:
        """Skip a step's own action but descend into the sub-branch
        containing the start node (used during first-pass skipping)."""
        if step.children:
            sig = self._walk(step.children)
            if sig != "continue":
                return sig
        for b in (step.branches or []):
            if self._start_in_branch(b):
                if b.node_id:
                    self._set(b.node_id, _ST_DONE)
                self._dim_other_branches(step, b)
                sig = self._walk(b.steps)
                if sig != "continue":
                    return sig
                return self._branch_loop_signal(b)
        return "continue"

    def _exec_step(self, step: StepConfig) -> str:
        """Execute one step; return ``"continue"`` | ``"end"`` | ``"stop"``."""
        t: str = step.type
        if t in ("keypress", "click"):
            self._press_step(step)
        elif t == "hold":
            self._hold_step(step)
        elif t == "press":
            self._key_down_step(step)
        elif t == "release":
            self._key_up_step(step)
        elif t == "wait":
            self._wait_step(step)
        elif t == "match":
            return self._match_step(step)
        elif t == "click_match":
            return self._click_match_step(step)
        # Non-match: recurse into children (branches belong to match only).
        if step.children:
            return self._walk(step.children)
        return "continue"

    def _match_step(self, step: StepConfig) -> str:
        """Execute a ``match`` step (branched or branchless).

        - **Branched + ``loop_until_match``**: loop {countdown,
          screenshot, branch-check} until a branch matches; see
          :meth:`_looping_branched_match`.
        - **Branched (one-shot)**: one countdown, one screenshot, pick
          the first matching branch; see :meth:`_oneshot_branched_match`.
        - **Branchless**: loop {countdown, screenshot} until the single
          feature is detected.
        """
        ftypes: list[str] = [f for f in (step.feature_type or "").split("/") if f]

        # Spec: missing feature → stop the run with a message.
        missing = [f for f in ftypes if not self._feature_ready(f)]
        if missing:
            labels = [
                (self.store.SLOT_LABELS.get(f, f) if self.store else f)
                for f in missing
            ]
            self._missing_msg = "、".join(labels)
            self._set(step.node_id, _ST_DONE)
            self._render()
            time.sleep(0.5)
            return "stop"

        fallback: float = self.store.settings.get(
            "global_threshold_fallback", 0.85,
        )

        if step.branches:
            if step.loop_until_match:
                return self._looping_branched_match(step, fallback)
            return self._oneshot_branched_match(step, fallback)

        # Branchless match: loop {countdown, screenshot} until detected.
        ftype = ftypes[0] if ftypes else None
        while True:
            self._set(step.node_id, _ST_CUR)
            self._render()
            self._countdown(step, step.delay)
            self._interrupted()
            conf: float = self.store.match_slot(ftype)[1] if ftype else 0.0
            if conf >= fallback:
                self._set(step.node_id, _ST_DONE)
                break
        return "continue"

    def _oneshot_branched_match(
        self, step: StepConfig, fallback: float,
    ) -> str:
        """One-shot branched match — one countdown, one screenshot.

        Picks the first branch whose feature matches.  If none matches,
        presses ``fallback_key`` (if any) and continues.
        """
        self._set(step.node_id, _ST_CUR)
        self._render()
        self._countdown(step, step.delay)
        self._interrupted()  # raises _PauseExit if interrupted
        self._set(step.node_id, _ST_DONE)

        taken: Branch | None = None
        for b in step.branches:
            ftype = self._branch_feature_type(b)
            if ftype and self.store.match_slot(ftype)[1] >= fallback:
                taken = b
                break

        if taken is None:
            # No branch matched → fallback
            self._dim_all_branches(step)
            if step.fallback_key:
                self._press_guarded(step.fallback_key)
            return "continue"

        self._dim_other_branches(step, taken)
        self._set(taken.node_id, _ST_DONE)
        sig = self._walk(taken.steps)
        if sig != "continue":
            return sig
        return self._branch_loop_signal(taken)

    def _looping_branched_match(
        self, step: StepConfig, fallback: float,
    ) -> str:
        """Looping branched match — loop until a branch matches.

        On each iteration: countdown, check branches in order, take the
        first whose feature matches.  After executing the branch:

        - ``loop == "结束"`` → end the run (``found++``).
        - ``loop == "回到本步骤"`` → reset subtree statuses and loop
          again (re-detect).
        - otherwise (``None`` / ``"回到①"`` / ...) → return
          ``"continue"`` (propagate to the outer loop).

        If no branch matches on an iteration, loop again (no fallback).
        """
        while True:
            # Reset subtree statuses for this iteration.
            self._reset_runtime_status([step])

            self._set(step.node_id, _ST_CUR)
            self._render()
            self._countdown(step, step.delay)
            self._interrupted()

            taken: Branch | None = None
            for b in step.branches:
                ftype = self._branch_feature_type(b)
                if ftype and self.store.match_slot(ftype)[1] >= fallback:
                    taken = b
                    break

            if taken is None:
                # No branch matched yet — loop again.
                continue

            self._set(step.node_id, _ST_DONE)
            self._dim_other_branches(step, taken)
            self._set(taken.node_id, _ST_DONE)
            sig = self._walk(taken.steps)
            if sig != "continue":
                return sig

            # Branch completed — resolve its loop label.
            if taken.loop == "结束":
                self.stats["found"] += 1
                return "end"
            if taken.loop == "回到本步骤":
                # Loop back to this match step (re-detect).
                continue
            # None / "回到①" / ... → propagate "continue".
            return "continue"

    def _branch_loop_signal(self, b: Branch) -> str:
        """Resolve a branch's loop label to an executor signal."""
        if b.loop == "结束":
            self.stats["found"] += 1
            return "end"
        return "continue"

    # ── Step action primitives ────────────────────────────

    def _press_step(self, step: StepConfig) -> None:
        """Mark CUR → countdown → press key → mark DONE."""
        self._set(step.node_id, _ST_CUR)
        self._render()
        self._countdown(step, step.delay)
        self._interrupted()
        if not self._guard.check():
            raise _PauseExit()
        if step.key:
            pyautogui.press(step.key)
        self._set(step.node_id, _ST_DONE)

    def _hold_step(self, step: StepConfig) -> None:
        """Mark CUR → keyDown → countdown (hold duration) → keyUp → DONE."""
        self._set(step.node_id, _ST_CUR)
        self._render()
        if not self._guard.check():
            raise _PauseExit()
        if step.key:
            pyautogui.keyDown(step.key)
        try:
            self._countdown(step, step.delay)
        finally:
            if step.key:
                pyautogui.keyUp(step.key)
        self._set(step.node_id, _ST_DONE)

    def _key_down_step(self, step: StepConfig) -> None:
        """Mark CUR → countdown → keyDown → DONE (no keyUp).

        Used for ``press`` type steps that hold a key indefinitely
        (until a later ``release`` step lifts it).
        """
        self._set(step.node_id, _ST_CUR)
        self._render()
        self._countdown(step, step.delay)
        self._interrupted()
        if not self._guard.check():
            raise _PauseExit()
        if step.key:
            pyautogui.keyDown(step.key)
        self._set(step.node_id, _ST_DONE)

    def _key_up_step(self, step: StepConfig) -> None:
        """Mark CUR → countdown → keyUp → DONE (no keyDown).

        Used for ``release`` type steps — lifts a key that was
        previously pressed with a ``press`` step.
        """
        self._set(step.node_id, _ST_CUR)
        self._render()
        self._countdown(step, step.delay)
        self._interrupted()
        if not self._guard.check():
            raise _PauseExit()
        if step.key:
            pyautogui.keyUp(step.key)
        self._set(step.node_id, _ST_DONE)

    def _wait_step(self, step: StepConfig) -> None:
        """Mark CUR → countdown → DONE."""
        self._set(step.node_id, _ST_CUR)
        self._render()
        self._countdown(step, step.delay)
        self._interrupted()
        self._set(step.node_id, _ST_DONE)

    def _press_guarded(self, key_name: str, interval: float = 0.15) -> None:
        """Press a key without status tracking (fallback path)."""
        if not self._guard.check():
            raise _PauseExit()
        pyautogui.press(key_name)
        time.sleep(interval)

    def _interrupted(self) -> None:
        """Raise :class:`_PauseExit` if the user interrupted or focus was lost."""
        key = try_get_key()
        if key is not None and key in (K.ESC, K.BS, K.ENTER):
            raise _PauseExit()
        if not self._guard.check():
            raise _PauseExit()

    def _set(self, node_id: str | None, status: str) -> None:
        """Update runtime status on a node by id."""
        if not node_id:
            return
        node = self._node_map.get(node_id)
        if node is not None:
            node.runtime_status = status

    def _dim_branch(self, b: Branch) -> None:
        """Dim a branch and its entire subtree."""
        if b.node_id:
            self._set(b.node_id, _ST_DIM)
        for nid in self._subtree_node_ids(b.steps):
            self._set(nid, _ST_DIM)

    def _dim_other_branches(self, match_step: StepConfig, taken: Branch) -> None:
        """Dim every branch of *match_step* except *taken*."""
        for b in (match_step.branches or []):
            if b is not taken:
                self._dim_branch(b)

    def _dim_all_branches(self, match_step: StepConfig) -> None:
        """Dim every branch of *match_step* (fallback / no match)."""
        for b in (match_step.branches or []):
            self._dim_branch(b)

    def _render(self) -> None:
        """Re-render the current frame (running or paused)."""
        self.render_idle(run_state=self._run_state)

    def _click_match_step(self, step: StepConfig) -> str:
        """Execute a ``click_match`` step — loop match then mouse-click.

        Loops {countdown, screenshot, template-match} until the feature
        is found, then clicks the matched region centre.
        """
        ftypes: list[str] = [f for f in (step.feature_type or "").split("/") if f]
        ftype: str | None = ftypes[0] if ftypes else None

        # Missing feature → stop.
        if ftype and not self._feature_ready(ftype):
            feat_label = self._match_feature_label(step)
            self._missing_msg = feat_label
            self._set(step.node_id, _ST_DONE)
            self._render()
            time.sleep(0.5)
            return "stop"

        fallback: float = self.store.settings.get(
            "global_threshold_fallback", 0.85,
        )

        while True:
            self._set(step.node_id, _ST_CUR)
            self._render()
            self._countdown(step, step.delay)
            self._interrupted()

            slot, conf, cx, cy = self.store.locate_template(ftype or "")
            if slot is not None and conf >= fallback:
                pyautogui.click(cx, cy, button=step.button, clicks=step.clicks)
                self._set(step.node_id, _ST_DONE)
                return "continue"

            self._interrupted()
            time.sleep(0.05)

    def _countdown(self, step: StepConfig, delay: float) -> None:
        """Count down *delay* seconds, rendering remaining ms every 50 ms.

        Checks for user interrupt (Esc/Backspace/Enter) and focus loss
        between frames — either raises :class:`_PauseExit`.

        When *delay* <= 0, enters **pause mode**: the run state is set
        to idle, runtime statuses are cleared, the cursor is placed on
        this step, and the executor blocks until the user presses Enter
        (resume) or Esc/Backspace (terminate).  During the pause the
        user may navigate the step list and adjust delays.
        """
        if delay <= 0:
            # 0 ms = pause
            self._run_state = "idle"
            self._reset_runtime_status(self._steps_cache)
            self.right_view = "steps"
            self.col_focus = "right"
            self.right_editing = True  # pause = direct edit mode
            self._sync_right_nav()
            # Position the cursor on this step.
            nav_idx: int = 0
            for i, (s, _t) in enumerate(self._iter_nav_steps(self._steps_cache)):
                if s is step:
                    nav_idx = i
                    break
            self.right_nav.index = nav_idx
            self._render()
            while True:
                key = get_key()
                if key in (K.ESC, K.BS):
                    raise _PauseExit()
                if key == K.ENTER:
                    break
                if key in (K.UP, K.DOWN):
                    self.right_nav.handle(key)
                elif key in (K.LEFT, K.RIGHT, K.SHIFT_LEFT, K.SHIFT_RIGHT):
                    delta = (
                        1000 if key in (K.SHIFT_LEFT, K.SHIFT_RIGHT) else 10
                    )
                    sign = -1 if key in (K.LEFT, K.SHIFT_LEFT) else 1
                    self._adjust_step_delay(self.right_nav.index, sign * delta)
                self._render()
            self._run_state = "running"
            self.right_editing = False
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
            step.runtime_remaining_ms = remaining
            self._render()
            to_deadline: float = deadline - time.monotonic()
            if to_deadline <= 0:
                break
            time.sleep(min(step_ms / 1000.0, to_deadline))
            key = try_get_key()
            if key is not None and key in (K.ESC, K.BS, K.ENTER):
                raise _PauseExit()
            if not self._guard.check():
                raise _PauseExit()
        step.runtime_remaining_ms = None

    # ── Start-node (run-from-row) helpers ─────────────────

    def _start_in_subtree(self, step: StepConfig) -> bool:
        """Return True if the start node is inside *step*'s subtree."""
        if not self._start_node:
            return False
        for nid in self._subtree_node_ids(
            (step.children or []) + [
                s for b in (step.branches or []) for s in b.steps
            ]
        ):
            if nid == self._start_node:
                return True
        # Also check branch node_ids themselves.
        for b in (step.branches or []):
            if b.node_id == self._start_node:
                return True
        return False

    def _start_in_branch(self, b: Branch) -> bool:
        """Return True if the start node is inside branch *b*."""
        if not self._start_node:
            return False
        if b.node_id == self._start_node:
            return True
        return self._start_node in self._subtree_node_ids(b.steps)


# ── Module-level utilities ────────────────────────────────

def calc_width(
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
