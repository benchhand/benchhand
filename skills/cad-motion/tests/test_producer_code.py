#!/usr/bin/env python3
"""A producer's reuse key covers its own script, the modules it loads and the data it reads (#128).

Each motion producer that publishes, or reports what decided a result, takes the sha256 of its code
and of its declared VERDICT_DATA from code_shas.closure. Editing either must change the recipe a
rerun is matched by, so the rerun refuses (another recipe needs --replace) or recomputes instead of
returning the old bundle. The edit is simulated by changing the bytes code_shas reads for one file.
A guard checks that each producer's VERDICT_DATA covers every data file its code names. Kernel-free.
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


ISO286 = SKILLS / "cad-fits" / "data" / "iso286.json"
ISO286_IT6_EDIT = (b'"6": [6, ', b'"6": [7, ')  # one table value: IT6 up to 3 mm, 6 um -> 7 um


@contextlib.contextmanager
def edited(path: Path, change: tuple[bytes, bytes] | None = None) -> Iterator[None]:
    """code_shas reads `path` edited: one comment line appended, or the one `change` (old, new) applied."""
    target, read = path.resolve(), code_shas._read

    def read_edited(candidate: Path) -> bytes:
        data = read(candidate)
        if Path(candidate).resolve() != target:
            return data
        if change is None:
            return data + b"\n# edited\n"
        assert data.count(change[0]) == 1, f"{path} does not hold {change[0]!r} once"
        return data.replace(*change)

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
        """Each edit of `paths`, and one ISO 286 table value, changes the recipe."""
        before = key(recipe_of())
        self.assertEqual(key(recipe_of()), before, "the recipe is not stable without an edit")
        for path, change in (*((path, None) for path in paths), (ISO286, ISO286_IT6_EDIT)):
            with self.subTest(edited=str(path.relative_to(SKILLS))), edited(path, change):
                self.assertNotEqual(key(recipe_of()), before)


def covered(path: Path, declared: tuple[Path, ...]) -> bool:
    path = path.resolve()
    return any(path == entry.resolve() or path.is_relative_to(entry.resolve()) for entry in declared)


class DeclaredDataTests(unittest.TestCase):
    def test_each_producer_declares_every_data_file_its_code_names(self) -> None:
        for module, script in ((play, "play.py"), (sweep, "sweep.py"), (merge_sweeps, "merge_sweeps.py"),
                               (spot_check, "spot_check.py")):
            with self.subTest(producer=script):
                missing = sorted(str(path.relative_to(SKILLS)) for path in code_shas.referenced_data(SCRIPTS / script)
                                 if not covered(path, module.VERDICT_DATA))
                self.assertEqual(missing, [], f"add these to {script} VERDICT_DATA")

    def test_a_data_file_a_module_names_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir=SKILLS / "cad-motion") as folder:
            data = Path(folder) / "table.json"
            data.write_text("{}", encoding="utf-8")
            script = Path(folder) / "producer.py"
            script.write_text('from pathlib import Path\nTABLE = Path(__file__).resolve().parent / "table.json"\n',
                              encoding="utf-8")
            self.assertEqual(code_shas.referenced_data(script), {data.resolve()})


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

    def test_declared_data_is_hashed_with_the_code(self) -> None:
        table = code_shas.closure(SCRIPTS / "sweep.py", sweep.VERDICT_DATA)
        self.assertIn("cad-fits/data/iso286.json", table)
        self.assertNotIn("cad-fits/data/iso286.json", code_shas.closure(SCRIPTS / "sweep.py"))

    def test_an_import_code_shas_cannot_place_is_an_error(self) -> None:
        for source, message in (("import no_such_module_anywhere\n", "no_such_module_anywhere"),
                                ("from . import pose\n", "relative import")):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                script = Path(folder) / "producer.py"
                script.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(code_shas.CodeShaError, message):
                    code_shas.closure(script)

    def test_a_script_imported_from_another_skill_folder_is_followed(self) -> None:
        table = code_shas.closure(SKILLS / "cad-model" / "scripts" / "cadmodel" / "fonts.py")
        self.assertIn("cad-flow/scripts/preflight.py", table)

    def test_a_path_to_a_missing_module_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "producer.py"
            script.write_text('from pathlib import Path\nHERE = Path(__file__).resolve().parent\n'
                              'GONE = HERE / "renamed_away.py"\n', encoding="utf-8")
            with self.assertRaisesRegex(code_shas.CodeShaError, "renamed_away.py"):
                code_shas.closure(script)


if __name__ == "__main__":
    unittest.main()
