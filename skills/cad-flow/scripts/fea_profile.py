# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Whether the project chose FEA: hw/product.yaml flow.profile.fea.enabled.

It reads the file with flow_profile.py's `read_product` (cad-record's one YAML reader) and the setting
with cad-record's gate accessor `fea_setting`, so preflight and setup answer as `flow_profile.py check`
does. It needs PyYAML only once a product file exists, so preflight imports it when it reads a project.
`load_flow_profile` is the one loader of flow_profile.py for preflight and setup.

`read` separates two cases. An absent key (no product file, no `flow`, no `profile`, no `fea`) is
configuration that was never made: off and valid. A key that is there with the wrong shape (a list, a
null, a string, a non-boolean `enabled`), a document that does not parse and a file that cannot be read
are invalid: off, with a message that names the file and the cause.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent


def load_flow_profile() -> Any:
    """flow_profile.py beside this file, loaded once. A load that fails leaves nothing registered, so the
    next call tries again and sees the same error."""
    name = "cad_flow_profile"
    if name not in sys.modules:
        path = SCRIPTS / "flow_profile.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load flow_profile: {path}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(sys.modules[name])
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def _kind(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def read(cwd: Path) -> tuple[bool, str | None]:
    """(enabled, invalid) for the project at `cwd`: invalid is None, or the file and the cause."""
    hw = Path(cwd) / "hw"
    path = hw / "product.yaml"
    if not path.exists():
        return False, None
    try:
        flow_profile = load_flow_profile()
    except ImportError as error:
        return False, f"{path}: cannot read it, a module it needs is missing: {error}"
    try:
        product = flow_profile.read_product(hw)
    except (flow_profile.ProfileError, ValueError) as error:  # ValueError: a date that is not a date
        text = str(error)
        return False, text if str(path) in text else f"{path}: cannot read: {text}"
    if product is None:  # an empty file
        return False, None
    node: Any = product
    trail = ""
    for key in ("flow", "profile", "fea"):
        if not isinstance(node, Mapping):
            return False, f"{path}: {trail or 'the document'} must be a mapping, not {_kind(node)}"
        if key not in node:
            return False, None
        node, trail = node[key], f"{trail}.{key}" if trail else key
    gate = flow_profile.gate
    enabled, _ = gate.fea_setting({gate.FEA: node})
    if enabled is None:
        return False, (f"{path}: flow.profile.fea must be a mapping with a boolean {gate.FEA_ENABLED} "
                       f"(true or false), not {_kind(node)}")
    return enabled, None
