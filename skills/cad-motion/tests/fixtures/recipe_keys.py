#!/usr/bin/env python3
"""Print the reuse keys of the cad-motion producers in a skills tree, as JSON.

Usage: python recipe_keys.py SKILLS_TREE

Loads play.py, sweep.py, merge_sweeps.py and spot_check.py from SKILLS_TREE/cad-motion/scripts, so a test
can copy the skills tree, edit one file of the copy and compare the keys. Each key is layout's
recipe_sha256 of the producer's recipe (spot_check: of the provenance its report prints), for a stand-in
version: play with its assessment and packet emission stubbed, sweep and merge_sweeps through their recipe
functions. Kernel-free.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock


def load(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(tree: Path) -> dict[str, str]:
    scripts = tree / "cad-motion" / "scripts"
    pose = load("cad_motion_pose", scripts / "pose.py")
    sweep = load("cad_motion_sweep", scripts / "sweep.py")
    play = load("cad_motion_play", scripts / "play.py")
    merge_sweeps = load("cad_motion_merge_sweeps", scripts / "merge_sweeps.py")
    spot_check = load("cad_motion_spot_check", scripts / "spot_check.py")
    key = pose.LAYOUT.recipe_sha256
    with tempfile.TemporaryDirectory() as hw, mock.patch.object(pose, "input_sha256", return_value="0" * 64):
        args = sweep.build_parser().parse_args(["--hw", hw, "--version", "v1"])
        recipe = sweep.recipe_of(SimpleNamespace(args=args, change=None, version_dir=Path(hw)),
                                 SimpleNamespace(digest=b"rule"))
        captured: list[dict[str, Any]] = []

        def emit(*emitted: Any, **_: Any) -> tuple[dict[str, Any], str]:
            captured.append(emitted[8])
            return {}, "pass"

        with mock.patch.object(play, "assess", return_value=({"dependencies": {"record": "0" * 64}}, {})), \
                mock.patch.object(play.check_packet, "emit", emit), contextlib.redirect_stdout(io.StringIO()):
            assert play.main(["--hw", hw, "--step", str(Path(hw) / "assembly.step")]) == 0
    inputs = [SimpleNamespace(manifest_sha256="1" * 64), SimpleNamespace(manifest_sha256="2" * 64)]
    return {"play": key(captured[0]), "sweep": key(recipe), "sweep_equivalence": key(sweep.equivalence_of(recipe)),
            "merge_sweeps": key(merge_sweeps.merged_recipe({"equivalence": sweep.equivalence_of(recipe)}, "sweep", inputs)),
            "spot_check": key(spot_check.audit_provenance({"rule_sha256": "0" * 64}, SimpleNamespace(digest=b"audit")))}


if __name__ == "__main__":
    json.dump(main(Path(sys.argv[1]).resolve()), sys.stdout)
