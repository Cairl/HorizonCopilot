"""Feature store for auction vehicle detection — four fixed slot data model.

Manages exactly four fixed :class:`FeatureSlot` instances (car_present,
car_absent, auction_success, auction_failure) with automatic migration
from v3 (multi-feature list) to v4 (fixed slots) config format.

Usage::

    store = FeatureStore(DATA_DIR)
    store.load()
    store.load_all_templates()

    for slot in store:
        print(slot.feature_type, slot.has_template())
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


# ── Default steps for migrated configs ────────────────────

_DEFAULT_STEPS: list[dict] = [
    {"name": "Enter (搜索)", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "Enter", "type": "keypress", "key": "enter", "delay": 0.05},
    {"name": "等待加载", "type": "wait", "delay": 0.8},
    {
        "name": "截图识别",
        "type": "match",
        "feature_type": "car_present/car_absent",
        "delay": 0.0,
    },
]


@dataclass
class FeatureSlot:
    """A single feature slot — one of four fixed types for auction matching.

    Attributes:
        feature_type: Slot identifier — one of ``"car_present"``,
            ``"car_absent"``, ``"auction_success"``, ``"auction_failure"``.
        region: Screen region tuple (x, y, w, h) for screenshot capture,
            or ``None`` if not set.
        template_file: Template image filename relative to data/ directory,
            or ``None`` if not set.
        threshold: Confidence threshold for this slot (0.0-1.0).
        template_image: Loaded template as RGB numpy array, or ``None``
            if not yet loaded / file missing.
    """

    feature_type: str
    region: tuple[int, int, int, int] | None = None
    template_file: str | None = None
    threshold: float = 0.85
    template_image: np.ndarray | None = None

    def has_template(self) -> bool:
        """Return ``True`` if the template image has been loaded."""
        return self.template_image is not None

    def status_str(self) -> str:
        """Return a human-readable status string for UI display."""
        return "已截取" if self.has_template() else "未截取"


class FeatureStore:
    """Fixed four-slot feature store with disk persistence (v4 config format).

    Manages exactly four :class:`FeatureSlot` instances:

    ===================  ==========
    ``feature_type``     中文标签
    ===================  ==========
    ``car_present``      有车状态
    ``car_absent``       无车状态
    ``auction_success``  抢车成功
    ``auction_failure``  抢车失败
    ===================  ==========

    v4 format (current)::

        {
            "version": 4,
            "slots": {
                "car_present": {"region": [100,200,300,50],
                                "template_file": "car_present.png",
                                "threshold": 0.85},
                "car_absent": {"region": null, "template_file": null,
                               "threshold": 0.85},
                "auction_success": null,
                "auction_failure": null
            },
            "settings": {"global_threshold_fallback": 0.85}
        }

    Usage::

        store = FeatureStore(DATA_DIR)
        store.load()
        store.load_all_templates()

        store.set_slot("car_present", (100, 200, 300, 50),
                       "car_present.png", 0.85)
        store.save()

        for slot in store:
            print(slot.feature_type, slot.status_str())
    """

    SLOT_TYPES: list[str] = [
        "car_present",
        "car_absent",
        "auction_success",
        "auction_failure",
    ]

    SLOT_LABELS: dict[str, str] = {
        "car_present": "有车状态",
        "car_absent": "无车状态",
        "auction_success": "抢车成功",
        "auction_failure": "抢车失败",
    }

    def __init__(self, data_dir: Path) -> None:
        """Initialise store bound to a data directory.

        Args:
            data_dir: Directory containing config.json and template images.
        """
        self.data_dir = Path(data_dir)
        self.config_file = self.data_dir / "config.json"
        self.slots: dict[str, FeatureSlot] = {
            t: FeatureSlot(feature_type=t) for t in self.SLOT_TYPES
        }
        self._steps: list[dict] = []
        self._settings: dict = {"global_threshold_fallback": 0.85}

    # ── Properties ──────────────────────────────────────────

    @property
    def settings(self) -> dict:
        """Return a shallow copy of the settings dict."""
        return dict(self._settings)

    @property
    def steps(self) -> list[dict]:
        """Return a shallow copy of the steps list."""
        return list(self._steps)

    # ── I/O ─────────────────────────────────────────────────

    def load(self) -> None:
        """Load config from disk (v4 format).

        Auto-detects format version and migrates as needed:
        - v1 / v2 / v3 → v4 (via :meth:`_migrate_to_v4`)
        - v4 direct load

        Does **not** overwrite the file unless :meth:`save` is called.
        """
        if not self.config_file.exists():
            return

        with open(self.config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        version = cfg.get("version", 0)
        if version < 4:
            self._migrate_to_v4(cfg)
            # Re-read the migrated file
            with open(self.config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)

        # v4 load
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
        """Persist all slots, steps, and settings to config.json (v4 format)."""
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
            "steps": self._steps if self._steps else _DEFAULT_STEPS,
            "settings": self._settings,
        }

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def save_steps(self, steps_list: list[dict]) -> None:
        """Save the steps array to the in-memory config and persist to disk.

        Args:
            steps_list: List of step dicts with ``name``, ``type``, ``key``,
                        ``delay``, ``feature_type``, and optional ``children``.
        """
        self._steps = steps_list
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Reload current config to preserve slots + settings
        cfg = self._load_current_config()

        cfg["version"] = 4
        cfg["steps"] = steps_list

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def load_steps(self) -> list[dict]:
        """Load steps from the config file (re-read from disk).

        Returns:
            The current steps list.
        """
        cfg = self._load_current_config()
        self._steps = cfg.get("steps", [])
        return self._steps

    # ── Slot management ─────────────────────────────────────

    def get_slot(self, feature_type: str) -> FeatureSlot | None:
        """Get a feature slot by type string.

        Args:
            feature_type: One of ``"car_present"``, ``"car_absent"``,
                          ``"auction_success"``, ``"auction_failure"``.

        Returns:
            The :class:`FeatureSlot`, or ``None`` if the type is unknown.
        """
        return self.slots.get(feature_type)

    def set_slot(
        self,
        feature_type: str,
        region: tuple[int, int, int, int],
        template_file: str,
        threshold: float = 0.85,
    ) -> None:
        """Set the data for a specific feature slot.

        Args:
            feature_type: Slot type string (must be in :attr:`SLOT_TYPES`).
            region: Screen region as (x, y, w, h).
            template_file: Filename (relative to data_dir) for the template PNG.
            threshold: Confidence threshold (0.0-1.0).
        """
        slot = self.slots[feature_type]
        slot.region = region
        slot.template_file = template_file
        slot.threshold = threshold

    def clear_slot(self, feature_type: str) -> None:
        """Clear the data for a specific feature slot.

        Also deletes the associated template file from disk.

        Args:
            feature_type: Slot type string (must be in :attr:`SLOT_TYPES`).
        """
        slot = self.slots[feature_type]
        old_file = slot.template_file
        slot.region = None
        slot.template_file = None
        slot.template_image = None
        if old_file:
            (self.data_dir / old_file).unlink(missing_ok=True)

    def load_all_templates(self) -> None:
        """Load every slot's template image from disk into memory.

        Slots whose template files are missing on disk will keep
        ``template_image = None``.
        """
        for slot in self.slots.values():
            if slot.template_file:
                tpl_path = self.data_dir / slot.template_file
                if tpl_path.exists():
                    img = cv2.imread(str(tpl_path))
                    if img is not None:
                        slot.template_image = cv2.cvtColor(
                            img, cv2.COLOR_BGR2RGB
                        )
                    else:
                        slot.template_image = None
                else:
                    slot.template_image = None

    # ── Sequence protocol ───────────────────────────────────

    def __len__(self) -> int:
        return 4

    def __getitem__(self, index: int) -> FeatureSlot:
        return self.slots[self.SLOT_TYPES[index]]

    def __iter__(self):
        for t in self.SLOT_TYPES:
            yield self.slots[t]

    # ── Internal helpers ────────────────────────────────────

    def _load_current_config(self) -> dict:
        """Read the config file fresh and return the parsed dict (empty if missing)."""
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
        """Migrate any pre-v4 config to v4 in-place and persist.

        Handles:
        - v1 (single-template ``region`` / ``threshold``)
        - v2 (multi-feature list without ``feature_type``)
        - v3 (multi-feature list with ``feature_type``)

        Migration strategy:
        1. Backup old config as ``config.v3.bak.json``.
        2. Destroy old features list.
        3. For each feature in the old list, try to map by ``feature_type``
           into the corresponding slot.  If multiple features share the same
           type, the last one wins.
        4. Features with ``feature_type=None`` are discarded.
        5. Write the new v4 config file.

        Args:
            cfg: The parsed config dict (modified in-place to v4).
        """
        # Backup old config
        bak_path = self.config_file.with_name("config.v3.bak.json")
        shutil.copy2(self.config_file, bak_path)

        # Extract old features from whatever version
        old_features: list[dict] = cfg.get("features", [])

        # Handle v1 (single region/threshold, no version field)
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

        # Map old features into slots (last-wins for same type)
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

        # Preserve steps if they exist, else use defaults
        steps = cfg.get("steps", [])
        if not steps:
            steps = _DEFAULT_STEPS

        # Preserve settings
        settings = cfg.get("settings", self._settings)

        # Write v4 config
        v4_cfg = {
            "version": 4,
            "slots": slot_data,
            "steps": steps,
            "settings": settings,
        }

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(v4_cfg, f, indent=2, ensure_ascii=False)
