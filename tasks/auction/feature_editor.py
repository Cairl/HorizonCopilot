"""Feature editor — interactive slot template capture workflow.

Provides :func:`capture_slot_feature` which guides the user through
region selection and template capture for a specific feature slot.
No type selection or naming steps needed — the slot type is predetermined.
"""

from __future__ import annotations

import time
from pathlib import Path

import msvcrt

from udlrtui import C, K, Renderer, widgets as W

from .sniper import _select_region, _capture_template_to, _calc_width
from .feature_store import FeatureSlot, FeatureStore


def capture_slot_feature(
    renderer: Renderer,
    store: FeatureStore,
    data_dir: Path,
    feature_type: str,
) -> bool:
    """Capture a template for a specific feature slot.

    Workflow:
        1. Prompt user to drag-select a screen region.
        2. Capture the template image from the selected region.
        3. Save it as ``{feature_type}.png`` (e.g. ``car_present.png``).
        4. Call :meth:`store.set_slot` with the type, region, filename,
           and threshold from settings.

    No type selection or naming step is needed — the slot type is
    predetermined by *feature_type*.

    Args:
        renderer: UdlrTui ``Renderer`` for terminal output.
        store: The :class:`FeatureStore` to update the slot in.
        data_dir: Directory where template PNGs are stored.
        feature_type: Slot type string (e.g. ``"car_present"``).

    Returns:
        ``True`` if a template was captured, ``False`` if the user
        cancelled (Esc at region selection).
    """
    label: str = store.SLOT_LABELS.get(feature_type, feature_type)
    title: str = f"截取{label}"

    w: int = _calc_width(
        [f"请在屏幕上拖拽框选{label}的特征区域", "框选完毕自动截取，Esc 取消"],
        title, 36,
    )

    # ── Step 1: region selection ─────────────────────────
    renderer.reset()
    _render_prompt(renderer, title, w,
                   f"{C.YELLOW}请在屏幕上拖拽框选{label}的特征区域{C.RESET}",
                   f"{C.GRAY}框选完毕自动截取，Esc 取消{C.RESET}")
    time.sleep(0.5)

    region = _select_region()
    if region is None:
        return False

    # ── Step 2: capture template ─────────────────────────
    filename: str = f"{feature_type}.png"
    filepath: Path = data_dir / filename
    _capture_template_to(region, filepath)

    _render_prompt(renderer, title, w,
                   f"{C.GREEN}区域已捕获: {region[2]}x{region[3]}{C.RESET}",
                   f"{C.LABEL}模板已保存到 {filename}{C.RESET}")

    # ── Step 3: save to store ────────────────────────────
    threshold: float = store.settings.get("global_threshold_fallback", 0.85)
    store.set_slot(feature_type, region, filename, threshold)
    return True


def _render_prompt(
    renderer: Renderer, title: str, w: int,
    line1: str, line2: str = ""
) -> None:
    """Render a simple two-line info panel."""
    ls: list[str] = [
        W.top_border(title, w),
        W.line(line1, w),
    ]
    if line2:
        ls.append(W.line(line2, w))
    ls.append(W.bottom_border(w))
    renderer.render(ls)
