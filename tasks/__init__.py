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
            "tag": "auction",
            "task_class": AuctionTask,
        }

    Returns:
        A list of ``task_info`` dicts, sorted by directory name.
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

    return tasks
