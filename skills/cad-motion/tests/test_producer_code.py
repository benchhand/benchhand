#!/usr/bin/env python3
"""A producer's reuse key covers its own script and the modules it loads (#128).

Each motion producer that publishes, or reports what decided a result, takes the sha256 of its code
from code_shas.closure. Editing that code must change the recipe a rerun is matched by, so the rerun
refuses (another recipe needs --replace) or recomputes instead of returning the old bundle. The edit
is simulated by appending a comment to the bytes code_shas reads for one file. Kernel-free.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
SKILLS = HERE.parents[1]
sys.path.insert(0, str(SKILLS.parent / "scripts"))
from modload import load_module as load  # noqa: E402

code_shas = load("cad_motion_code_shas", SCRIPTS / "code_shas.py")
pose = load("cad_motion_pose", SCRIPTS / "pose.py")
sweep = load("cad_motion_sweep", SCRIPTS / "sweep.py")
spot_check = load("cad_motion_spot_check", SCRIPTS / "spot_check.py")
merge_sweeps = load("cad_motion_merge_sweeps", SCRIPTS / "merge_sweeps.py")
play = load("cad_motion_play", SCRIPTS / "play.py")


@contextlib.contextmanager
def edited(path: Path) -> Iterator[None]:
    """code_shas reads `path` with one comment line appended, as after an edit that changes no behavior."""
    target, read = path.resolve(), code_shas._read

    def read_edited(candidate: Path) -> bytes:
        data = read(candidate)
        return data + b"\n# edited\n" if Path(candidate).resolve() == target else data

    code_shas._closure.cache_clear()   # code_shas reads each script once per process
    try:
        with mock.patch.object(code_shas, "_read", read_edited):
            yield
    finally:
        code_shas._closure.cache_clear()


def key(recipe: Any) -> str:
    """The reuse key: layout matches a bundle by the sha256 of its canonical recipe."""
    return pose.LAYOUT.recipe_sha256(recipe)


class Case(unittest.TestCase):
    def assert_edit_changes(self, recipe_of: Any, *paths: Path) -> None:
        before = key(recipe_of())
        self.assertEqual(key(recipe_of()), before, "the recipe is not stable without an edit")
        for path in paths:
            with self.subTest(edited=str(path.relative_to(SKILLS))), edited(path):
                self.assertNotEqual(key(recipe_of()), before)


class PlayTests(Case):
    def recipe(self) -> dict[str, Any]:
        captured: list[dict[str, Any]] = []

        def emit(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
            captured.append(args[8])
            return {}, "pass"

        with tempfile.TemporaryDirectory() as hw, \
                mock.patch.object(play, "assess", return_value=({"dependencies": {"record": "0" * 64}}, {})), \
                mock.patch.object(play.check_packet, "emit", emit), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(play.main(["--hw", hw, "--step", str(Path(hw) / "assembly.step")]), 0)
        [recipe] = captured
        return recipe

    def test_editing_play_or_a_module_it_loads_changes_its_packet_recipe(self) -> None:
        self.assert_edit_changes(self.recipe, SCRIPTS / "play.py", SCRIPTS / "plan.py")


def sweep_recipe() -> dict[str, Any]:
    """sweep.recipe_of for a default sweep of a stand-in version."""
    with tempfile.TemporaryDirectory() as hw, mock.patch.object(pose, "input_sha256", return_value="0" * 64):
        args = sweep.build_parser().parse_args(["--hw", hw, "--version", "v1"])
        return sweep.recipe_of(SimpleNamespace(args=args, change=None, version_dir=Path(hw)),
                               SimpleNamespace(digest=b"rule"))


class SweepTests(Case):
    def recipe(self) -> dict[str, Any]:
        return sweep_recipe()

    def test_editing_sweep_or_a_module_it_loads_changes_its_bundle_recipe(self) -> None:
        self.assert_edit_changes(self.recipe, SCRIPTS / "sweep.py", SKILLS / "cad-fits" / "scripts" / "fits.py",
                                 SKILLS / "cad-record" / "scripts" / "assembly_data.py")

    def test_editing_sweep_or_a_module_it_loads_changes_the_equivalence_a_merge_checks(self) -> None:
        self.assert_edit_changes(lambda: sweep.equivalence_of(self.recipe()), SCRIPTS / "sweep.py",
                                 SKILLS / "cad-fits" / "scripts" / "fits.py")


class MergeTests(Case):
    def setUp(self) -> None:
        # The sweep sealed the recipe its inputs share. At merge time code_shas reads only the merge's own code.
        self.provenance = {"equivalence": sweep.equivalence_of(sweep_recipe())}
        self.inputs = [SimpleNamespace(manifest_sha256="1" * 64), SimpleNamespace(manifest_sha256="2" * 64)]

    def recipe(self) -> dict[str, Any]:
        return merge_sweeps.merged_recipe(self.provenance, "sweep", self.inputs)

    def test_editing_merge_sweeps_changes_the_merged_bundle_recipe(self) -> None:
        self.assert_edit_changes(self.recipe, SCRIPTS / "merge_sweeps.py")


class SpotCheckTests(Case):
    def provenance(self) -> dict[str, Any]:
        return spot_check.audit_provenance({"rule_sha256": "0" * 64}, SimpleNamespace(digest=b"audit"))

    def test_editing_spot_check_changes_what_its_report_says_decided_it(self) -> None:
        self.assert_edit_changes(self.provenance, SCRIPTS / "spot_check.py")


class ClosureTests(unittest.TestCase):
    def test_a_script_covers_itself_and_the_sibling_files_it_loads(self) -> None:
        table = code_shas.closure(SCRIPTS / "play.py")
        for name in ("cad-motion/scripts/play.py", "cad-motion/scripts/pose.py", "cad-motion/scripts/sweep.py",
                     "cad-fits/scripts/fits.py", "cad-record/scripts/yaml_loader.py"):
            self.assertIn(name, table)

    def test_a_path_to_a_missing_module_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "producer.py"
            script.write_text('from pathlib import Path\nHERE = Path(__file__).resolve().parent\n'
                              'GONE = HERE / "renamed_away.py"\n', encoding="utf-8")
            with self.assertRaisesRegex(code_shas.CodeShaError, "renamed_away.py"):
                code_shas.closure(script)


if __name__ == "__main__":
    unittest.main()
