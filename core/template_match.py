"""OpenCV 模板匹配 — 项目级通用工具。

对屏幕截图和模板图片执行灰度模板匹配，返回置信度。

用法::

    from core.template_match import match_template

    confidence = match_template(screenshot_np, template_np)
    if confidence >= 0.85:
        ...
"""

from __future__ import annotations

import cv2
import numpy as np


def match_template(screenshot: np.ndarray, template: np.ndarray) -> float:
    """对截图和模板执行模板匹配，返回最大置信度 (0.0-1.0)。

    将两张图片转为灰度后使用 ``cv2.TM_CCOEFF_NORMED`` 匹配。
    若模板大于截图区域，返回 0.0。

    Args:
        screenshot: 截图 numpy 数组（RGB 或灰度）。
        template: 模板 numpy 数组（RGB 或灰度）。

    Returns:
        匹配置信度，范围 0.0 到 1.0。
    """
    if len(screenshot.shape) == 3:
        scr_gray = cv2.cvtColor(screenshot, cv2.COLOR_RGB2GRAY)
    else:
        scr_gray = screenshot
    if len(template.shape) == 3:
        tpl_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    else:
        tpl_gray = template
    th, tw = tpl_gray.shape[:2]
    sh, sw = scr_gray.shape[:2]
    if th > sh or tw > sw:
        return 0.0
    result = cv2.matchTemplate(scr_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return float(max_val)
