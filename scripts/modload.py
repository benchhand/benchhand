# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Shared test helpers: load a script by path, and read a producer's enveloped stdout.

Tests put this directory on sys.path (see any skills/*/tests file)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


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

def result_payload(stdout: str) -> Any:
    """The payload of a producer's stdout: parse the tool-result envelope, check its shape, return `payload`."""
    document = json.loads(stdout)
    assert isinstance(document, dict), f"stdout is not a JSON object: {stdout[:200]!r}"
    assert set(document) == {"schema", "status", "findings", "payload"}, f"not a tool-result envelope: {sorted(document)}"
    assert str(document["schema"]).startswith("tool-result/"), document["schema"]
    assert isinstance(document["status"], str) and isinstance(document["findings"], list), document
    return document["payload"]
