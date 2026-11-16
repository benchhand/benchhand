"""Load a script by path for a test. Tests put this directory on sys.path (see any skills/*/tests file)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_fresh(name: str, path: Path) -> ModuleType:
    """Execute `path` as module `name`, registered in sys.modules before it runs, replacing any earlier one."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(name: str, path: Path) -> ModuleType:
    """One module per name for the whole test run: scripts load their siblings under these names, so a second
    copy would split their exception and record classes."""
    return sys.modules[name] if name in sys.modules else load_fresh(name, path)
