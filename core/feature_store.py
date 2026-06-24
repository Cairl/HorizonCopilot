"""通用特征槽位存储 — 项目级共享基础设施。

管理一组 :class:`FeatureSlot` 实例，支持磁盘持久化（v4 JSON 格式）、
模板图片加载、以及单槽位模板匹配。

与各任务特化代码解耦：槽位类型和标签由构造参数注入，各任务自行定义。

用法::

    from core.feature_store import FeatureStore

    store = FeatureStore(
        data_dir=Path("data"),
        slot_types=["car_present", "car_absent", ...],
        slot_labels={"car_present": "有车状态", ...},
    )
    store.load()
    store.load_all_templates()

    slot, conf = store.match_slot("car_present")
    if conf >= 0.85:
        ...
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyautogui


# ── Slot categories ──────────────────────────────────────
# 特征槽位分两类，对应两种步骤的用途：
#
# * ``CATEGORY_MONITOR`` (监测特征) — ``match`` 步骤用。特征出现在
#   已知固定位置（截取时框定的 region），运行时只在该区域截图判断
#   是否出现，用于判断画面进入了哪个状态。
#
# * ``CATEGORY_LOCATOR`` (定位特征) — ``click_match`` 步骤用。特征
#   出现位置不固定，运行时全屏截图搜索（``locate_template_fullscreen``），
#   找到后点击匹配中心。
CATEGORY_MONITOR: str = "monitor"
CATEGORY_LOCATOR: str = "locator"

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_MONITOR: "监测特征",
    CATEGORY_LOCATOR: "定位特征",
}

# 渲染顺序：监测特征在前，定位特征在后。
CATEGORY_ORDER: list[str] = [CATEGORY_MONITOR, CATEGORY_LOCATOR]


@dataclass
class FeatureSlot:
    """单个特征槽位 — 一个可识别的屏幕状态。

    Attributes:
        feature_type: 槽位标识符（如 ``"car_present"``）。
        region: 屏幕区域 ``(x, y, w, h)``，或 ``None``。
        template_file: 模板图片文件名（相对于 data_dir），或 ``None``。
        threshold: 置信度阈值 (0.0-1.0)。
        template_image: 已加载的模板 numpy 数组（RGB），或 ``None``。
    """

    feature_type: str
    region: tuple[int, int, int, int] | None = None
    template_file: str | None = None
    threshold: float = 0.85
    template_image: np.ndarray | None = None

    def has_template(self) -> bool:
        """模板图片是否已加载。"""
        return self.template_image is not None

    def status_str(self) -> str:
        """UI 显示用的状态字符串。"""
        return "已截取" if self.has_template() else "未截取"


class FeatureStore:
    """通用特征槽位存储，支持磁盘持久化和模板匹配。

    槽位类型和标签由调用方注入，不绑定任何具体任务。

    v4 配置格式::

        {
            "version": 4,
            "slots": {
                "car_present": {"region": [100,200,300,50],
                                "template_file": "car_present.png",
                                "threshold": 0.85},
                "car_absent": null,
                ...
            },
            "steps": [...],
            "settings": {"global_threshold_fallback": 0.85}
        }

    Args:
        data_dir: 数据目录（含 config.json 和模板图片）。
        slot_types: 槽位类型列表，如 ``["car_present", "car_absent", ...]``。
        slot_labels: 槽位中文标签映射，如 ``{"car_present": "有车状态"}``。
        default_steps: 迁移旧配置时使用的默认步骤列表（可为空）。
        slot_categories: 槽位类别映射，如
            ``{"subaru_factory": "locator"}``。未指定的槽位默认为
            :data:`CATEGORY_MONITOR`。类别决定特征库视图的分组和
            ``click_match`` 步骤是否要求 ``region``。
    """

    def __init__(
        self,
        data_dir: Path,
        slot_types: list[str],
        slot_labels: dict[str, str],
        default_steps: list[dict] | None = None,
        slot_categories: dict[str, str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.config_file = self.data_dir / "config.json"
        self.SLOT_TYPES: list[str] = list(slot_types)
        self.SLOT_LABELS: dict[str, str] = dict(slot_labels)
        self.SLOT_CATEGORIES: dict[str, str] = {
            t: (slot_categories or {}).get(t, CATEGORY_MONITOR)
            for t in self.SLOT_TYPES
        }
        self._default_steps: list[dict] = list(default_steps) if default_steps else []
        self.slots: dict[str, FeatureSlot] = {
            t: FeatureSlot(feature_type=t) for t in self.SLOT_TYPES
        }
        self._steps: list[dict] = []
        self._settings: dict = {"global_threshold_fallback": 0.85}

    # ── Properties ──────────────────────────────────────────

    @property
    def settings(self) -> dict:
        """返回 settings 字典的浅拷贝。"""
        return dict(self._settings)

    @property
    def steps(self) -> list[dict]:
        """返回 steps 列表的浅拷贝。"""
        return list(self._steps)

    # ── I/O ─────────────────────────────────────────────────

    def load(self) -> None:
        """从磁盘加载配置（v4 格式）。

        自动检测格式版本并迁移：
        - v1 / v2 / v3 → v4（通过 :meth:`_migrate_to_v4`）
        - v4 直接加载

        不会覆盖文件，除非调用 :meth:`save`。
        """
        if not self.config_file.exists():
            return

        with open(self.config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        version = cfg.get("version", 0)
        if version < 4:
            self._migrate_to_v4(cfg)
            with open(self.config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)

        slots_data = cfg.get("slots", {})
        for feature_type in self.SLOT_TYPES:
            slot_data = slots_data.get(feature_type)
            if slot_data is not None:
                region_raw = slot_data.get("region")
                region: tuple[int, int, int, int] | None = (
                    tuple(region_raw) if region_raw else None
                )
                self.slots[feature_type] = FeatureSlot(
                    feature_type=feature_type,
                    region=region,
                    template_file=slot_data.get("template_file"),
                    threshold=slot_data.get("threshold", 0.85),
                )
            else:
                self.slots[feature_type] = FeatureSlot(feature_type=feature_type)

        self._steps = cfg.get("steps", [])
        self._settings = cfg.get(
            "settings", {"global_threshold_fallback": 0.85}
        )

    def save(self) -> None:
        """持久化所有槽位、步骤和设置到 config.json（v4 格式）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        slots_data: dict = {}
        for feature_type in self.SLOT_TYPES:
            slot = self.slots[feature_type]
            if slot.region is not None and slot.template_file is not None:
                slots_data[feature_type] = {
                    "region": list(slot.region),
                    "template_file": slot.template_file,
                    "threshold": slot.threshold,
                }
            else:
                slots_data[feature_type] = None

        cfg = {
            "version": 4,
            "slots": slots_data,
            "steps": self._steps if self._steps else self._default_steps,
            "settings": self._settings,
        }

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def save_steps(self, steps_list: list[dict]) -> None:
        """保存步骤数组到内存配置并持久化到磁盘。

        Args:
            steps_list: 步骤字典列表。
        """
        self._steps = steps_list
        self.data_dir.mkdir(parents=True, exist_ok=True)

        cfg = self._load_current_config()
        cfg["version"] = 4
        cfg["steps"] = steps_list

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def load_steps(self) -> list[dict]:
        """从磁盘重新读取步骤列表。"""
        cfg = self._load_current_config()
        self._steps = cfg.get("steps", [])
        return self._steps

    # ── Slot management ─────────────────────────────────────

    def get_slot(self, feature_type: str) -> FeatureSlot | None:
        """按类型获取特征槽位。"""
        return self.slots.get(feature_type)

    def set_slot(
        self,
        feature_type: str,
        region: tuple[int, int, int, int],
        template_file: str,
        threshold: float = 0.85,
    ) -> None:
        """设置指定槽位的数据。"""
        slot = self.slots[feature_type]
        slot.region = region
        slot.template_file = template_file
        slot.threshold = threshold

    def clear_slot(self, feature_type: str) -> None:
        """清除指定槽位的数据并删除模板文件。"""
        slot = self.slots[feature_type]
        old_file = slot.template_file
        slot.region = None
        slot.template_file = None
        slot.template_image = None
        if old_file:
            (self.data_dir / old_file).unlink(missing_ok=True)

    def load_all_templates(self) -> None:
        """从磁盘加载所有槽位的模板图片到内存。"""
        for slot in self.slots.values():
            if slot.template_file:
                tpl_path = self.data_dir / slot.template_file
                if tpl_path.exists():
                    # 使用 np.fromfile + cv2.imdecode 替代 cv2.imread，
                    # 解决 OpenCV 在 Windows 上不支持中文路径的问题
                    try:
                        img_array = np.fromfile(str(tpl_path), dtype=np.uint8)
                        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                    except Exception:
                        img = None
                    if img is not None:
                        slot.template_image = cv2.cvtColor(
                            img, cv2.COLOR_BGR2RGB
                        )
                    else:
                        slot.template_image = None
                else:
                    slot.template_image = None

    # ── Template matching ───────────────────────────────────

    def match_slot(self, feature_type: str) -> tuple[FeatureSlot | None, float]:
        """对指定槽位截图并执行模板匹配。

        Args:
            feature_type: 槽位类型字符串。

        Returns:
            ``(slot, confidence)`` — 槽位对象和匹配置信度，
            或 ``(None, 0.0)``（槽位不存在 / 无模板 / 匹配失败）。
        """
        from core.template_match import match_template

        slot = self.slots.get(feature_type)
        if slot is None or not slot.has_template() or slot.region is None:
            return None, 0.0

        x, y, w, h = slot.region
        try:
            screenshot = pyautogui.screenshot(region=(x, y, w, h))
            scr_np: np.ndarray = np.array(screenshot)
            conf: float = match_template(scr_np, slot.template_image)
            return slot, conf
        except Exception:
            return None, 0.0

    def locate_template(
        self, feature_type: str,
    ) -> tuple[FeatureSlot | None, float, int, int]:
        """截图匹配并返回最佳匹配点在屏幕上的绝对坐标。

        仅在 ``slot.region`` 指定的固定区域内截图匹配 —— 用于
        ``match`` 步骤（特征出现在已知位置的场景）。

        Returns:
            ``(slot, confidence, abs_x, abs_y)`` —
            *slot* 为匹配的槽位（或 ``None``），*confidence* 为
            置信度 (0.0-1.0)，*abs_x* / *abs_y* 为匹配区域中心
            在屏幕上的绝对像素坐标。匹配失败时返回
            ``(None, 0.0, 0, 0)``。
        """
        from core.template_match import locate_template as _loc

        slot = self.slots.get(feature_type)
        if slot is None or not slot.has_template() or slot.region is None:
            return None, 0.0, 0, 0

        rx, ry, rw, rh = slot.region
        try:
            screenshot = pyautogui.screenshot(region=(rx, ry, rw, rh))
            scr_np: np.ndarray = np.array(screenshot)
            conf, mx, my = _loc(scr_np, slot.template_image)
            tw: int = slot.template_image.shape[1]
            th: int = slot.template_image.shape[0]
            cx: int = rx + mx + tw // 2
            cy: int = ry + my + th // 2
            return slot, conf, cx, cy
        except Exception:
            return None, 0.0, 0, 0

    def locate_template_fullscreen(
        self, feature_type: str,
    ) -> tuple[FeatureSlot | None, float, int, int]:
        """全屏截图匹配并返回最佳匹配点在屏幕上的绝对坐标。

        与 :meth:`locate_template` 不同，此方法不依赖 ``slot.region``，
        而是截取整个屏幕后做模板匹配 —— 用于 ``click_match`` 步骤
        （特征可能出现在屏幕任意位置、需要先找到再点击的场景）。

        仅要求槽位有模板图片，``slot.region`` 可为 ``None``。

        Returns:
            ``(slot, confidence, abs_x, abs_y)`` — 含义同
            :meth:`locate_template`，但坐标基于全屏截图。
        """
        from core.template_match import locate_template as _loc

        slot = self.slots.get(feature_type)
        if slot is None or not slot.has_template():
            return None, 0.0, 0, 0

        try:
            screenshot = pyautogui.screenshot()
            scr_np: np.ndarray = np.array(screenshot)
            conf, mx, my = _loc(scr_np, slot.template_image)
            tw: int = slot.template_image.shape[1]
            th: int = slot.template_image.shape[0]
            cx: int = mx + tw // 2
            cy: int = my + th // 2
            return slot, conf, cx, cy
        except Exception:
            return None, 0.0, 0, 0

    # ── Sequence protocol ───────────────────────────────────

    def __len__(self) -> int:
        return len(self.SLOT_TYPES)

    def __getitem__(self, index: int) -> FeatureSlot:
        return self.slots[self.SLOT_TYPES[index]]

    def __iter__(self):
        for t in self.SLOT_TYPES:
            yield self.slots[t]

    # ── Category helpers ───────────────────────────────────

    def slot_category(self, feature_type: str) -> str:
        """返回槽位的类别（``CATEGORY_MONITOR`` / ``CATEGORY_LOCATOR``）。

        未注册的 *feature_type* 视为监测特征。
        """
        return self.SLOT_CATEGORIES.get(feature_type, CATEGORY_MONITOR)

    def iter_by_category(self):
        """按类别分组产出槽位（先监测后定位，组内按 SLOT_TYPES 顺序）。

        特征库视图用此方法渲染分组；``list(store)`` 仍按原始
        ``SLOT_TYPES`` 顺序（与 config.json 一致）。
        """
        for cat in CATEGORY_ORDER:
            for t in self.SLOT_TYPES:
                if self.SLOT_CATEGORIES.get(t) == cat:
                    yield self.slots[t]

    # ── Internal helpers ────────────────────────────────────

    def _load_current_config(self) -> dict:
        """读取配置文件并返回解析后的字典（不存在则返回空框架）。"""
        if not self.config_file.exists():
            return {
                "version": 4,
                "slots": {t: None for t in self.SLOT_TYPES},
                "steps": [],
                "settings": self._settings,
            }
        with open(self.config_file, "r", encoding="utf-8") as f:
            return json.load(f)

    # ── Migration ───────────────────────────────────────────

    def _migrate_to_v4(self, cfg: dict) -> None:
        """将 v4 之前的配置迁移到 v4 并持久化。

        处理：
        - v1（单模板 ``region`` / ``threshold``）
        - v2（多特征列表，无 ``feature_type``）
        - v3（多特征列表，有 ``feature_type``）

        迁移策略：
        1. 备份旧配置为 ``config.v3.bak.json``。
        2. 销毁旧 features 列表。
        3. 按 ``feature_type`` 映射到对应槽位（同类型后者覆盖前者）。
        4. ``feature_type=None`` 的特征被丢弃。
        5. 写入新 v4 配置文件。
        """
        bak_path = self.config_file.with_name("config.v3.bak.json")
        shutil.copy2(self.config_file, bak_path)

        old_features: list[dict] = cfg.get("features", [])

        if not old_features and cfg.get("region") is not None:
            old_features = [
                {
                    "name": "默认车辆",
                    "region": cfg["region"],
                    "template_file": "template.png",
                    "threshold": cfg.get("threshold", 0.90),
                    "feature_type": None,
                }
            ]

        slot_data: dict[str, dict | None] = {t: None for t in self.SLOT_TYPES}
        for feat in old_features:
            ftype = feat.get("feature_type")
            if ftype is None or ftype not in self.SLOT_TYPES:
                continue
            slot_data[ftype] = {
                "region": feat["region"],
                "template_file": feat["template_file"],
                "threshold": feat.get("threshold", 0.85),
            }

        steps = cfg.get("steps", [])
        if not steps:
            steps = self._default_steps

        settings = cfg.get("settings", self._settings)

        v4_cfg = {
            "version": 4,
            "slots": slot_data,
            "steps": steps,
            "settings": settings,
        }

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(v4_cfg, f, indent=2, ensure_ascii=False)
