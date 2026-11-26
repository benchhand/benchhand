# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Whether the project chose FEA: hw/product.yaml flow.profile.fea.enabled.

It reads the file with flow_profile.py's `read_product` (cad-record's one YAML reader) and the
profile with cad-record's gate accessors, so preflight and setup answer as `flow_profile.py check`
does. It needs PyYAML, so preflight loads it by path only when it reads a project.

`read` separates two cases. Missing configuration (no product file, no flow.profile, no fea answer
yet, or `enabled: false`) is off and valid. Configuration that is there but cannot be read, or has
the wrong shape, is off with a message that names the file and the cause.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent


def _flow_profile() -> Any:
    """flow_profile.py beside this file, loaded once under the name setup.py uses."""
    name = "cad_flow_profile"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / "flow_profile.py")
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load flow_profile: {SCRIPTS / 'flow_profile.py'}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def read(cwd: Path) -> tuple[bool, str | None]:
    """(enabled, invalid) for the project at `cwd`: invalid is None, or the file and the cause."""
    flow_profile = _flow_profile()
    gate = flow_profile.gate
    hw = Path(cwd) / "hw"
    path = hw / "product.yaml"
    if not path.exists():
        return False, None
    try:
        product = flow_profile.read_product(hw)
    except flow_profile.ProfileError as error:
        return False, str(error)
    profile = gate.profile_of(product)
    if profile is None:
        return False, None
    if not isinstance(profile, Mapping):
        return False, f"{path}: flow.profile must be a mapping, not {type(profile).__name__}"
    if gate.FEA not in profile:
        return False, None
    enabled, _ = gate.fea_setting(profile)
    if enabled is None:
        return False, (f"{path}: flow.profile.fea must be a mapping with a boolean {gate.FEA_ENABLED} "
                       "(true or false)")
    return enabled, None
