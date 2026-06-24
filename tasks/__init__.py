"""Task discovery — scan ``tasks/`` subdirectories for task modules.

:func:`discover_tasks` finds all subdirectories containing an
``__init__.py`` with a ``task_info`` dictionary and returns a
list of discovered task descriptors.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path


def discover_tasks() -> list[dict]:
    """Scan ``tasks/`` subdirectories for ``task_info`` modules.

    Each subdirectory must have an ``__init__.py`` that exports
    a ``task_info`` dict with at least these keys::

        task_info = {
            "label": "拍卖场抢车",
            "tag": "拍卖场抢车",
            "task_class": AuctionTask,
        }

    Returns:
        A list of ``task_info`` dicts.  ``拍卖场抢车`` is always pinned
        first; the rest follow by directory name, so newly added tasks
        default to appearing below the pinned one.
    """
    tasks: list[dict] = []
    task_dir = Path(__file__).parent

    for entry in sorted(task_dir.iterdir()):
        if entry.is_dir() and (entry / "__init__.py").exists():
            module_name: str = f"tasks.{entry.name}"
            try:
                module = import_module(module_name)
                if hasattr(module, "task_info"):
                    tasks.append(module.task_info)
            except Exception:
                continue

    # Pin 拍卖场抢车 first, then 购买斯巴鲁抽奖; keep the rest in
    # alphabetical (directory-name) order.
    _PIN_ORDER = ["auction", "subaru"]
    pinned: list[dict] = []
    for tag in _PIN_ORDER:
        for t in tasks:
            if t.get("tag") == tag:
                pinned.append(t)
    rest: list[dict] = [
        t for t in tasks if t.get("tag") not in _PIN_ORDER
    ]
    return pinned + rest
