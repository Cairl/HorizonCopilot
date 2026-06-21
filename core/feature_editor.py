"""特征截取工作流 — 项目级通用模块。

提供 :func:`capture_slot_feature`，引导用户完成区域选择和模板截取。
槽位类型由调用方指定，无需类型选择或命名步骤。

用法::

    from core.feature_editor import capture_slot_feature

    success = capture_slot_feature(renderer, store, data_dir, "car_present")
"""

from __future__ import annotations

import time
from pathlib import Path

from udlrtui import C, Renderer, widgets as W

from core.screen_capture import select_region, capture_template_to
from core.feature_store import FeatureStore
from core.task_base import calc_width


def capture_slot_feature(
    renderer: Renderer,
    store: FeatureStore,
    data_dir: Path,
    feature_type: str,
) -> bool:
    """为指定槽位截取模板。

    工作流：
        1. 提示用户拖拽框选屏幕区域。
        2. 从选定区域截取模板图片。
        3. 保存为 ``{feature_type}.png``。
        4. 调用 :meth:`store.set_slot` 写入类型、区域、文件名和阈值。

    无需类型选择或命名步骤 — 槽位类型由 *feature_type* 预定。

    Args:
        renderer: UdlrTui ``Renderer``。
        store: 要更新的 :class:`FeatureStore`。
        data_dir: 模板 PNG 存储目录。
        feature_type: 槽位类型字符串（如 ``"car_present"``）。

    Returns:
        ``True`` 截取成功，``False`` 用户取消。
    """
    label: str = store.SLOT_LABELS.get(feature_type, feature_type)
    title: str = f"截取{label}"

    w: int = calc_width(
        [f"请在屏幕上拖拽框选{label}的特征区域", "框选完毕自动截取，Esc 取消"],
        title, 36,
    )

    # ── Step 1: region selection ─────────────────────────
    renderer.reset()
    _render_prompt(renderer, title, w,
                   f"{C.YELLOW}请在屏幕上拖拽框选{label}的特征区域{C.RESET}",
                   f"{C.GRAY}框选完毕自动截取，Esc 取消{C.RESET}")
    time.sleep(0.5)

    region = select_region()
    if region is None:
        return False

    # ── Step 2: capture template ─────────────────────────
    filename: str = f"{feature_type}.png"
    filepath: Path = data_dir / filename
    capture_template_to(region, filepath)

    _render_prompt(renderer, title, w,
                   f"{C.GREEN}区域已捕获: {region[2]}x{region[3]}{C.RESET}",
                   f"{C.LABEL}模板已保存到 {filename}{C.RESET}")

    # ── Step 3: save to store ────────────────────────────
    threshold: float = store.settings.get("global_threshold_fallback", 0.85)
    store.set_slot(feature_type, region, filename, threshold)
    return True


def _render_prompt(
    renderer: Renderer, title: str, w: int,
    line1: str, line2: str = "",
) -> None:
    """渲染简单的两行提示面板。"""
    ls: list[str] = [
        W.top_border(title, w),
        W.line(line1, w),
    ]
    if line2:
        ls.append(W.line(line2, w))
    ls.append(W.bottom_border(w))
    renderer.render(ls)
