# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Load a script once under a shared name, safe for worker threads: the one loader that cad-record and the
skills above it (cad-drawings) use for a sibling module they reach by path. Standard library only."""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

_LOAD_LOCK = threading.RLock()


def load_shared(name: str, path: Path) -> Any:
    """Return `path` loaded as `name`. A thread that finds it loading waits for the finished module,
    and a failed load leaves nothing in sys.modules (the module goes there first for its dataclasses)."""
    with _LOAD_LOCK:
        module = sys.modules.get(name)
        if module is not None:
            return module
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name} at {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module
