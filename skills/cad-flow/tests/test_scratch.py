#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-flow scratch workspace tests: a worker's copy, build, and delivery never touch the lead's records.

Run: python3 -m unittest discover -s cad/skills/cad-flow/tests -v
The init and deliver tests are stdlib-only. The build test needs `uv`; it builds a copy of the
checkout's lamp example (seven CadQuery parts) in a scratch workspace, so a cold uv cache adds minutes.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = HERE.parent / "scripts" / "scratch.py"
sys.path.insert(0, str(HERE.parents[1] / "cad-drawings" / "scripts"))
import freecad_runtime  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from decision_fixture import add_flow_profile  # noqa: E402
from modload import result_payload  # noqa: E402
FREECADCMD = freecad_runtime.find_freecadcmd()
FREECAD_PRESENT = FREECADCMD is not None and FREECADCMD.is_file()

TEMPLATE = HERE.parents[1] / "cad-record" / "assets" / "project-template" / "hw"
VALIDATOR = HERE.parents[1] / "cad-record" / "scripts" / "validate.py"
LAMP_EXAMPLE = HERE.parents[2] / "examples" / "lamp-arm" / "hw"


def tree(root: Path) -> dict[str, str]:
    """Every file below `root` with its sha256, so a test can prove nothing changed."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def load_scratch():
    """A fresh scratch.py module, so a test can replace its tool runner."""
    spec = importlib.util.spec_from_file_location("cad_flow_scratch", SCRATCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScratchCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-scratch-test-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.hw = self.project / "hw"

    def use_generic_template(self) -> None:
        shutil.copytree(TEMPLATE, self.hw)
        part = self.hw / "parts" / "PRT-0001"
        part.mkdir(parents=True)
        # The template is a record version 2 record: a part states its manufacturing intent, here all unknown.
        intent = "".join(f"  {name}: {{status: unknown, reason: not recorded yet}}\n" for name in (
            "functions", "material_profile", "service", "critical_surfaces", "route"))
        (part / "part.yaml").write_text(
            "PN: PRT-0001\nrev: X1\ntitle: Placeholder part\nmaterial: Placeholder material\n"
            "process: cnc\nfinish: As machined\nmass_target: null\nmake_buy: make\nintent:\n" + intent, encoding="utf-8")
        result = subprocess.run([sys.executable, str(VALIDATOR), str(self.project)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result_payload(result.stdout), {"ok": True, "findings": []})

    def use_lamp_example(self) -> None:
        shutil.copytree(LAMP_EXAMPLE, self.hw)

    def scratch(self, *args: str, python: bool = True, timeout: int = 60) -> tuple[int, dict]:
        argv = [sys.executable, str(SCRATCH)] if python else ["uv", "run", "--quiet", "--script", str(SCRATCH)]
        done = subprocess.run([*argv, *args], cwd=self.project, capture_output=True, text=True, timeout=timeout)
        try:
            return done.returncode, json.loads(done.stdout)
        except ValueError:
            self.fail(f"no JSON on stdout (exit {done.returncode}):\n{done.stdout}\n{done.stderr[-4000:]}")

    def init(self, unit: str, *parts: str) -> dict:
        code, report = self.scratch("init", "--hw", "hw", "--unit", unit, *(f"--part={pn}" for pn in parts))
        self.assertEqual(code, 0, report)
        return report


class InitAndDeliver(ScratchCase):
    def setUp(self) -> None:
        super().setUp()
        self.use_generic_template()

    def test_init_copies_the_generic_record_without_versions_or_renders(self) -> None:
        report = self.init("part-base_sub", "PRT-0001")
        scratch_hw = Path(report["scratch_hw"])
        self.assertEqual(scratch_hw, (self.project / ".cad-work" / "part-base_sub" / "hw").resolve())
        self.assertFalse((scratch_hw / "versions").exists())
        self.assertFalse((scratch_hw / "renders").exists())
        self.assertEqual((self.project / ".cad-work" / ".gitignore").read_text(encoding="utf-8"), "*\n")
        baseline = json.loads((scratch_hw.parent / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["parts"], ["PRT-0001"])
        self.assertEqual(sorted(baseline["files"]), sorted(tree(self.hw)))

    @staticmethod
    def snapshot(root: Path) -> dict[str, str]:
        """Every path below `root`: a file's sha256, or <dir>, <symlink>. Nothing may appear, vanish or change."""
        found = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                found[relative] = "<symlink>"
            elif path.is_dir():
                found[relative] = "<dir>"
            else:
                found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return found

    def test_a_plain_init_over_a_workspace_without_baseline_refuses_and_changes_nothing(self) -> None:
        work = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"]).parent
        scratch_hw = work / "hw"
        (scratch_hw / "parts" / "PRT-0001" / "model.py").write_text("def build(params):\n    return None\n", encoding="utf-8")
        (scratch_hw / "parts" / "PRT-0001" / "notes.txt").write_text("work in progress\n", encoding="utf-8")
        (scratch_hw / "versions" / "v1").mkdir(parents=True)
        (scratch_hw / "versions" / "v1" / "manifest.json").write_text("{}\n", encoding="utf-8")
        (work / "logs").mkdir()
        (work / "logs" / "build.log").write_text("step 3 of 9\n", encoding="utf-8")
        (work / "baseline.json").unlink()
        before = self.snapshot(self.project / ".cad-work")
        self.assertIn("part-base_sub/hw/parts/PRT-0001/model.py", before)
        code, report = self.scratch("init", "--hw", "hw", "--unit", "part-base_sub", "--part", "PRT-0001")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("has no baseline.json", report["error"])
        self.assertEqual(self.snapshot(self.project / ".cad-work"), before)

    def test_a_plain_init_over_a_workspace_with_baseline_keeps_it_and_reset_replaces_it(self) -> None:
        work = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"]).parent
        note = work / "hw" / "parts" / "PRT-0001" / "notes.txt"
        note.write_text("work in progress\n", encoding="utf-8")
        before = self.snapshot(self.project / ".cad-work")
        code, report = self.scratch("init", "--hw", "hw", "--unit", "part-base_sub", "--part", "PRT-0001")
        self.assertEqual((code, report["status"]), (0, "exists"), report)
        self.assertEqual(self.snapshot(self.project / ".cad-work"), before)
        code, report = self.scratch("init", "--hw", "hw", "--unit", "part-base_sub", "--part", "PRT-0001", "--reset")
        self.assertEqual((code, report["status"]), (0, "created"), report)
        self.assertFalse(note.exists())

    def test_init_rejects_unsafe_units_and_malformed_part_numbers(self) -> None:
        for args in (("--unit", "../escape", "--part", "PRT-0001"), ("--unit", "ok", "--part", "bad"),
                     ("--unit", "ok")):
            with self.subTest(args=args):
                code, report = self.scratch("init", "--hw", "hw", *args)
                self.assertEqual((code, report["status"]), (2, "error"))
        self.assertFalse((self.project / ".cad-work").exists())


    def test_deliver_refuses_a_hidden_generated_subtree_without_writing(self) -> None:
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        generated = scratch_hw / "parts" / "PRT-0001" / ".generated" / "mesh.step"
        generated.parent.mkdir()
        generated.write_bytes(b"not a record source")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("non-record or non-canonical", report["error"])
        self.assertEqual(tree(self.hw), before)

    def test_deliver_refuses_an_unauthorized_new_part_without_writing(self) -> None:
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        unauthorized = scratch_hw / "parts" / "PRT-0002"
        unauthorized.mkdir()
        (unauthorized / "part.yaml").write_text("PN: PRT-0002\n", encoding="utf-8")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("not authorized for new part PRT-0002", report["error"])
        self.assertEqual(tree(self.hw), before)

    def test_deliver_adds_an_authorized_new_part_under_parts(self) -> None:
        scratch_hw = Path(self.init("part-base_sub", "PRT-0002")["scratch_hw"])
        part = scratch_hw / "parts" / "PRT-0002"
        self.assertTrue(part.is_dir())
        (part / "part.yaml").write_text(
            "PN: PRT-0002\nrev: X1\ntitle: New placeholder\nmaterial: Placeholder material\n"
            "process: cnc\nfinish: As machined\nmass_target: null\nmake_buy: make\n", encoding="utf-8")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertIn(str((self.hw / "parts" / "PRT-0002" / "part.yaml").resolve()), report["delivered"])
        self.assertEqual((self.hw / "parts" / "PRT-0002" / "part.yaml").read_text(encoding="utf-8"),
                         (part / "part.yaml").read_text(encoding="utf-8"))
        self.assertEqual(tree(self.hw)["parts/PRT-0001/part.yaml"], before["parts/PRT-0001/part.yaml"])
    def test_deliver_writes_only_the_units_parts_and_lists_the_rest_as_proposals(self) -> None:
        before = tree(self.hw)
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        (scratch_hw / "parts" / "PRT-0001" / "model.py").write_text("def build(params):\n    return None\n", encoding="utf-8")
        with (scratch_hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# proposed: widen IF-004 slot\n")
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(tree(self.hw), {**before, "parts/PRT-0001/model.py": hashlib.sha256(
            b"def build(params):\n    return None\n").hexdigest()})
        (scratch_hw / "parts" / "PRT-0001" / "notes.txt").write_text("work in progress\n", encoding="utf-8")
        again = self.init("part-base_sub", "PRT-0001")
        self.assertEqual(again["status"], "exists")
        self.assertTrue((scratch_hw / "parts" / "PRT-0001" / "notes.txt").is_file())
        code, reset = self.scratch("init", "--hw", "hw", "--unit", "part-base_sub", "--part", "PRT-0001", "--reset")
        self.assertEqual((code, reset["status"]), (0, "created"))
        self.assertFalse((scratch_hw / "parts" / "PRT-0001" / "notes.txt").exists())

    def test_deliver_refuses_a_part_changed_since_init_and_writes_nothing(self) -> None:
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        (scratch_hw / "parts" / "PRT-0001" / "model.py").write_text("# scratch rewrite\n", encoding="utf-8")
        record = self.hw / "parts" / "PRT-0001" / "part.yaml"
        record.write_text(record.read_text(encoding="utf-8").replace("mass_target: null", "mass_target: 10"), encoding="utf-8")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (1, "conflict"), report)
        self.assertEqual(report["changed_since_init"], [str(self.hw.resolve() / "parts" / "PRT-0001")])
        self.assertEqual(tree(self.hw), before)

    def test_deliver_ignores_python_bytecode_caches_and_never_copies_them(self) -> None:
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        part = scratch_hw / "parts" / "PRT-0001"
        (part / "model.py").write_text("def build(params):\n    return None\n", encoding="utf-8")
        for cache in (part / "__pycache__", scratch_hw / "__pycache__"):
            cache.mkdir()
            (cache / "model.cpython-312.pyc").write_bytes(b"generated bytecode")
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(report["proposals"], [])
        self.assertTrue((self.hw / "parts" / "PRT-0001" / "model.py").is_file())
        self.assertFalse((self.hw / "parts" / "PRT-0001" / "__pycache__").exists())
        self.assertFalse((self.hw / "__pycache__").exists())

    LEAD_CAD = (f".cad/cache/objects/{'a' * 64}", ".cad/cache/.gitignore", f".cad/cache/records/{'a' * 64}.json",
                ".cad/pairs-v2.sqlite", ".cad/pairs-v2.sqlite-wal", ".cad/pairs-v2.sqlite-shm", ".cad/.gitignore",
                ".cad/runs/3f2b9c1e8d7a4b6c9e0f1a2b3c4d5e6f.json", ".cad/recheck.json")

    def lead_caches(self) -> dict[str, str]:
        """The lead's hw/.cad/: the part store, the pair cache, a run record and the recheck stamp."""
        for rel in self.LEAD_CAD:
            (self.hw / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.hw / rel).write_text("lead\n", encoding="utf-8")
        return {rel: digest for rel, digest in tree(self.hw).items() if rel.startswith(".cad/")}

    def test_init_and_deliver_leave_hw_cad_out(self) -> None:
        """hw/.cad/ holds caches and control files only: init never copies or hashes them, and a scratch
        build's own .cad/ is never delivered."""
        before = self.lead_caches()
        report = self.init("part-base_sub", "PRT-0001")
        scratch_hw = Path(report["scratch_hw"])
        seeded = {p.relative_to(scratch_hw).as_posix() for p in (scratch_hw / ".cad").rglob("*") if p.is_file()}
        self.assertEqual(seeded, {f".cad/cache/objects/{'a' * 64}", f".cad/cache/records/{'a' * 64}.json"})  # links; no caches
        self.assertEqual(report["seeded_store_files"], 2)
        for rel in seeded:
            self.assertEqual((scratch_hw / rel).stat().st_ino, (self.hw / rel).stat().st_ino, rel)
        baseline = json.loads((scratch_hw.parent / "baseline.json").read_text(encoding="utf-8"))
        self.assertFalse([path for path in baseline["files"] if path.startswith(".cad/")])
        (scratch_hw / "parts" / "PRT-0001" / "model.py").write_text("def build(params):\n    return None\n",
                                                                     encoding="utf-8")
        for rel in (f".cad/cache/objects/{'b' * 64}", ".cad/pairs-v2.sqlite", ".cad/runs/x.json"):  # the scratch's own
            (scratch_hw / rel).parent.mkdir(parents=True, exist_ok=True)
            (scratch_hw / rel).write_text("scratch\n", encoding="utf-8")
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(report["proposals"], [])
        self.assertEqual({rel: d for rel, d in tree(self.hw).items() if rel.startswith(".cad/")}, before)

    @unittest.skipUnless(shutil.which("uv"), "the scratch build runs cad-model in its uv environments")
    def test_a_real_scratch_build_then_deliver_carries_only_the_part(self) -> None:
        """The lead builds v1; init seeds the scratch store with links to its objects, so the scratch build
        of the unchanged part reuses the lead's STEP. scratch build runs cad-model run and check, which
        write the scratch's own store, pair cache and run records under .cad/; after a model edit and a
        second scratch build, deliver carries only the part and leaves the lead's .cad/ and versions alone."""
        model = ('KERNEL = "cadquery"\nPARAMS = {"size": 12.0}\n\n\ndef build(params):\n    import cadquery as cq\n\n'
                 '    return cq.Workplane("XY").box(params["size"], params["size"], params["size"])\n')
        (self.hw / "parts" / "PRT-0001" / "model.py").write_text(model, encoding="utf-8")
        add_flow_profile(self.hw)  # `run` builds at LAYOUT at the earliest, so it needs a settled profile at any stage
        cad_model = HERE.parents[1] / "cad-model" / "scripts" / "cad_model.py"
        lead = subprocess.run(["uv", "run", "--quiet", "--script", str(cad_model), "run", "hw", "--label", "v1"],
                              cwd=self.project, capture_output=True, text=True, timeout=1800)
        self.assertEqual(lead.returncode, 0, lead.stderr[-3000:])
        before, versions = self.lead_caches(), tree(self.hw / "versions")
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        build_args = ("build", "--hw", "hw", "--unit", "part-base_sub", "--fidelity", "F0", "--kernel", "cadquery")
        code, report = self.scratch(*build_args, timeout=1800)
        self.assertEqual((report["run"]["exit"], report["check"]["exit"]), (0, 0), report)
        lead_step = (self.hw / "versions" / "v1" / "parts" / "PRT-0001.step").stat()
        scratch_step = (Path(report["parts_dir"]) / "PRT-0001.step").stat()
        self.assertEqual((scratch_step.st_dev, scratch_step.st_ino), (lead_step.st_dev, lead_step.st_ino))
        edited = model.replace('"size": 12.0', '"size": 14.0')
        (scratch_hw / "parts" / "PRT-0001" / "model.py").write_text(edited, encoding="utf-8")
        code, report = self.scratch(*build_args, timeout=1800)
        self.assertEqual((report["run"]["exit"], report["check"]["exit"]), (0, 0), report)
        written = {p.relative_to(scratch_hw).as_posix() for p in (scratch_hw / ".cad").rglob("*") if p.is_file()}
        self.assertTrue({".cad/pairs-v2.sqlite", ".cad/cache/.gitignore"} <= written, written)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(report["proposals"], [])
        self.assertEqual((self.hw / "parts" / "PRT-0001" / "model.py").read_text(encoding="utf-8"), edited)
        self.assertEqual({rel: d for rel, d in tree(self.hw).items() if rel.startswith(".cad/")}, before)
        self.assertEqual(tree(self.hw / "versions"), versions)

    def test_build_passes_each_attach_to_cad_model_run_and_reports_it(self) -> None:
        scratch_module = load_scratch()
        self.init("part-base_sub", "PRT-0001")
        calls = []

        def run_tool(argv: list[str], _log: Path) -> tuple[int, dict]:
            calls.append(argv)
            if argv[1] == "run":
                version = Path(argv[2]) / "versions" / argv[argv.index("--label") + 1]
                version.mkdir(parents=True)
                (version / "manifest.json").write_text("{}\n", encoding="utf-8")
                return 0, {"findings": []}
            if argv[1] == "check":
                return 0, {"blocking_failures": [], "warnings": []}
            return 0, {"findings": []}

        stdout = io.StringIO()
        with mock.patch.object(scratch_module, "run_tool", side_effect=run_tool), redirect_stdout(stdout):
            code = scratch_module.main(["build", "--hw", str(self.hw), "--unit", "part-base_sub", "--fidelity", "F2",
                                        "--attach", "PRT-0002=PRT-0001", "--attach", "@foot-1=PRT-0001"])
        report = json.loads(stdout.getvalue())
        self.assertEqual((code, report["status"]), (0, "pass"), report)
        self.assertEqual(report["attach"], ["PRT-0002=PRT-0001", "@foot-1=PRT-0001"])
        run = next(argv for argv in calls if argv[1] == "run")
        self.assertEqual(run[run.index("--attach"):], ["--attach", "PRT-0002=PRT-0001", "--attach", "@foot-1=PRT-0001"])

    def test_a_record_version_2_build_runs_the_process_checks_and_blocks_only_on_its_own_failures(self) -> None:
        """form_check runs as --version on a record version 2 scratch version: the process packet's failed
        outcomes of the unit's own part block it, another part's failures and its own unknowns do not."""
        scratch_module = load_scratch()
        self.init("part-base_sub", "PRT-0001")
        calls = []
        opened = [{"obligation": "process.fdm.support@PRT-0001/print", "outcome": "unknown"},
                  {"obligation": "route.consistency@PRT-0002", "outcome": "fail"}]

        def run_tool(argv: list[str], _log: Path) -> tuple[int, dict]:
            calls.append(argv)
            if argv[1] == "run":
                version = Path(argv[2]) / "versions" / argv[argv.index("--label") + 1]
                version.mkdir(parents=True)
                (version / "manifest.json").write_text('{"record_version": 2}\n', encoding="utf-8")
                return 0, {"findings": []}
            if argv[1] == "check":
                return 0, {"blocking_failures": [], "warnings": []}
            return 1, {"findings": [], "manufacturing": {"status": "fail", "complete": True, "bundle": "b", "open": opened}}

        with mock.patch.object(scratch_module, "run_tool", side_effect=run_tool):
            code, report = scratch_module.build(self.hw, "part-base_sub", "cadquery", "F2")
        form = next(argv for argv in calls if argv[0].endswith("form_check.py"))
        self.assertEqual(form[form.index("--version") + 1], report["label"])
        self.assertNotIn("--parts-dir", form)
        self.assertEqual((code, report["status"]), (0, "pass"), report)
        self.assertEqual((report["form"]["process"]["own_failures"], report["form"]["process"]["own_unknown"]),
                         ([], [opened[0]]))
        opened.append({"obligation": "process.fdm.geometry@PRT-0001/print", "outcome": "fail"})
        with mock.patch.object(scratch_module, "run_tool", side_effect=run_tool):
            code, report = scratch_module.build(self.hw, "part-base_sub", "cadquery", "F2")
        self.assertEqual((code, report["status"]), (1, "fail"), report)
        self.assertEqual(report["form"]["process"]["own_failures"], [opened[-1]])

    def test_build_then_deliver_ignores_generated_version_output(self) -> None:
        scratch_module = load_scratch()

        unit = "part-base_sub"
        code, initialized = scratch_module.init(self.hw, unit, ["PRT-0001"], reset=False)
        self.assertEqual(code, 0, initialized)
        scratch_hw = Path(initialized["scratch_hw"])
        model = scratch_hw / "parts" / "PRT-0001" / "model.py"
        model.write_text("def build(params):\n    return None\n", encoding="utf-8")

        def run_tool(argv: list[str], _log: Path) -> tuple[int, dict]:
            if argv[1] == "run":
                versions = Path(argv[2]) / "versions"
                label = argv[argv.index("--label") + 1]
                (versions / label).mkdir(parents=True)
                (versions / label / "manifest.json").write_text("{}\n", encoding="utf-8")
                (versions / "latest.json").write_text('{"version":"v1"}\n', encoding="utf-8")
                return 0, {"findings": []}
            if argv[1] == "check":
                return 0, {"blocking_failures": [], "warnings": []}
            return 0, {"findings": []}

        with mock.patch.object(scratch_module, "run_tool", side_effect=run_tool):
            build_code, build_report = scratch_module.build(self.hw, unit, "cadquery", "F2")
        self.assertEqual((build_code, build_report["status"]), (0, "pass"), build_report)
        self.assertTrue((scratch_hw / "versions" / "latest.json").is_file())

        deliver_code, deliver_report = scratch_module.deliver(self.hw, unit)
        self.assertEqual((deliver_code, deliver_report["status"]), (0, "delivered"), deliver_report)
        self.assertIn(str(self.hw / "parts" / "PRT-0001" / "model.py"), deliver_report["delivered"])
        self.assertTrue((self.hw / "parts" / "PRT-0001" / "model.py").is_file())


class RegeneratedOutputs(ScratchCase):
    def setUp(self) -> None:
        super().setUp()
        self.use_generic_template()

    def test_deliver_carries_the_record_and_reports_a_regenerated_bom_as_a_proposal(self) -> None:
        scratch_hw = Path(self.init("part-base_sub", "PRT-0001")["scratch_hw"])
        (scratch_hw / "parts" / "PRT-0001" / "model.py").write_text("def build(params):\n    return None\n", encoding="utf-8")
        with (scratch_hw / "bom.csv").open("a", encoding="utf-8") as stream:
            stream.write("regenerated by the unit\n")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "part-base_sub")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(tree(self.hw), {**before, "parts/PRT-0001/model.py": hashlib.sha256(
            b"def build(params):\n    return None\n").hexdigest()})
        self.assertEqual([(p["path"], p["change"], p.get("regenerated")) for p in report["proposals"]],
                         [(str(self.hw.resolve() / "bom.csv"), "modified", True)])


class Sync(ScratchCase):
    """`sync` takes the lead's integrated record into a workspace without making it the unit's own change."""

    PART = ("PN: {pn}\nrev: X1\ntitle: Placeholder\nmaterial: Placeholder material\nprocess: cnc\n"
            "finish: As machined\nmass_target: null\nmake_buy: make\n")

    def setUp(self) -> None:
        super().setUp()
        self.use_generic_template()

    def lead_part(self, pn: str) -> Path:
        part = self.hw / "parts" / pn
        part.mkdir(parents=True)
        (part / "part.yaml").write_text(self.PART.format(pn=pn), encoding="utf-8")
        return part

    def sync(self, unit: str) -> tuple[int, dict]:
        return self.scratch("sync", "--hw", "hw", "--unit", unit)

    def test_a_part_the_lead_retired_disappears_and_is_not_delivered_as_a_removal(self) -> None:
        self.lead_part("PRT-0003")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        self.assertTrue((scratch_hw / "parts" / "PRT-0003" / "part.yaml").is_file())
        shutil.rmtree(self.hw / "parts" / "PRT-0003")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (0, "synced"), report)
        self.assertEqual(report["removed"], ["parts/PRT-0003/part.yaml"])
        self.assertEqual(sorted(path.name for path in (scratch_hw / "parts").glob("PRT-*")), ["PRT-0001"])
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "unit-a")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(report["proposals"], [])
        self.assertEqual(tree(self.hw), before)

    def test_another_units_delivery_appears_and_deliver_does_not_carry_it(self) -> None:
        scratch_a = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        scratch_b = Path(self.init("unit-b", "PRT-0002")["scratch_hw"])
        (scratch_b / "parts" / "PRT-0002" / "part.yaml").write_text(self.PART.format(pn="PRT-0002"), encoding="utf-8")
        code, _ = self.scratch("deliver", "--hw", "hw", "--unit", "unit-b")
        self.assertEqual(code, 0)
        self.assertFalse((scratch_a / "parts" / "PRT-0002").exists())
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (0, "synced"), report)
        self.assertEqual(report["added"], ["parts/PRT-0002/part.yaml"])
        self.assertEqual((scratch_a / "parts" / "PRT-0002" / "part.yaml").read_text(encoding="utf-8"),
                         self.PART.format(pn="PRT-0002"))
        (scratch_a / "parts" / "PRT-0001" / "model.py").write_text("def build(params):\n    return None\n", encoding="utf-8")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "unit-a")
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(report["proposals"], [])
        self.assertEqual(tree(self.hw), {**before, "parts/PRT-0001/model.py": hashlib.sha256(
            b"def build(params):\n    return None\n").hexdigest()})
        self.assertFalse([path for path in report["delivered"] if "PRT-0002" in path])

    def test_a_unit_still_cannot_deliver_a_change_to_a_part_it_does_not_own(self) -> None:
        self.lead_part("PRT-0002")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        foreign = scratch_hw / "parts" / "PRT-0002" / "part.yaml"
        foreign.write_text(foreign.read_text(encoding="utf-8").replace("Placeholder", "Edited"), encoding="utf-8")
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "unit-a")
        self.assertEqual(code, 0, report)
        self.assertEqual(tree(self.hw), before)
        self.assertEqual([p["path"] for p in report["proposals"]], [str(self.hw.resolve() / "parts/PRT-0002/part.yaml")])

    def test_a_part_copied_in_by_hand_is_still_refused_as_an_unauthorized_new_part(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        self.lead_part("PRT-0002")
        shutil.copytree(self.hw / "parts" / "PRT-0002", scratch_hw / "parts" / "PRT-0002")
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", "unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("not authorized for new part PRT-0002", report["error"])

    def test_a_file_both_the_unit_and_the_lead_changed_is_kept_and_reported_as_a_conflict(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        for root, line in ((scratch_hw, "# unit\n"), (self.hw, "# lead\n")):
            with (root / "interfaces.yaml").open("a", encoding="utf-8") as stream:
                stream.write(line)
        self.lead_part("PRT-0002")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (1, "conflict"), report)
        self.assertEqual(report["conflicts"], ["interfaces.yaml"])
        self.assertEqual(report["added"], ["parts/PRT-0002/part.yaml"])
        self.assertTrue((scratch_hw / "interfaces.yaml").read_text(encoding="utf-8").endswith("# unit\n"))

    def deliver_and_expect_no_proposal(self, unit: str) -> None:
        before = tree(self.hw)
        code, report = self.scratch("deliver", "--hw", "hw", "--unit", unit)
        self.assertEqual((code, report["status"]), (0, "delivered"), report)
        self.assertEqual(report["proposals"], [])
        self.assertEqual(tree(self.hw), before)

    def test_a_lead_only_edit_to_a_shared_file_is_copied_in_and_is_not_proposed_back(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# lead widened IF-004\n")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["modified"]), (0, "synced", ["interfaces.yaml"]), report)
        self.assertEqual((scratch_hw / "interfaces.yaml").read_bytes(), (self.hw / "interfaces.yaml").read_bytes())
        self.deliver_and_expect_no_proposal("unit-a")

    def test_sync_links_the_leads_new_store_files_and_deliver_ignores_them(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        stored = [f".cad/cache/objects/{'b' * 64}", f".cad/cache/records/{'b' * 64}.json"]
        for rel in stored:
            (self.hw / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.hw / rel).write_text("lead\n", encoding="utf-8")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["seeded_store_files"]), (0, "synced", 2), report)
        for rel in stored:
            self.assertEqual((scratch_hw / rel).stat().st_ino, (self.hw / rel).stat().st_ino, rel)
        self.deliver_and_expect_no_proposal("unit-a")

    def outside(self) -> Path:
        """A directory outside every workspace that a scratch symlink can point at."""
        path = self.project / "outside"
        path.mkdir()
        (path / "keep.txt").write_text("keep\n", encoding="utf-8")
        return path

    def test_sync_refuses_a_linked_scratch_part_directory_and_deletes_nothing_through_it(self) -> None:
        self.lead_part("PRT-0003")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        shutil.rmtree(scratch_hw / "parts" / "PRT-0003")
        (outside / "part.yaml").write_text("outside\n", encoding="utf-8")
        (scratch_hw / "parts" / "PRT-0003").symlink_to(outside)
        shutil.rmtree(self.hw / "parts" / "PRT-0003")
        before = tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("symlink", report["error"])
        self.assertEqual(tree(outside), before)

    def test_sync_refuses_a_linked_scratch_directory_and_writes_nothing_through_it(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        shutil.rmtree(scratch_hw / "manufacturing")
        (scratch_hw / "manufacturing").symlink_to(outside)
        (self.hw / "manufacturing" / "lead-note.yaml").write_text("lead\n", encoding="utf-8")
        before = tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertEqual(tree(outside), before)

    def test_sync_refuses_a_linked_scratch_cache_directory_and_seeds_nothing_through_it(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        (scratch_hw / ".cad").symlink_to(outside)
        rel = f".cad/cache/objects/{'c' * 64}"
        (self.hw / rel).parent.mkdir(parents=True)
        (self.hw / rel).write_text("lead\n", encoding="utf-8")
        before = tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertEqual(tree(outside), before)

    def test_sync_refuses_while_a_delivery_is_staged_and_names_it(self) -> None:
        """deliver renames a part directory away while it replaces it, so a scan then would read a replacement as a retirement."""
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        staged = self.hw / "parts" / ".PRT-0002.deliver-4242"
        staged.mkdir()
        shutil.rmtree(self.hw / "parts" / "PRT-0001")
        before = tree(scratch_hw)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("delivery in progress", report["error"])
        self.assertIn(".PRT-0002.deliver-4242", report["error"])
        self.assertEqual(tree(scratch_hw), before)

    def test_sync_refuses_when_the_lead_changes_a_file_between_its_two_snapshot_scans(self) -> None:
        self.init("unit-a", "PRT-0001")
        module = load_scratch()
        hw, scans = self.hw.resolve(), []
        real = module.delivery_tree

        def scan(root):
            result = real(root)
            if root == hw:
                scans.append(result)
                if len(scans) == 1:
                    with (hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
                        stream.write("# lead edit during the scan\n")
            return result

        with mock.patch.object(module, "delivery_tree", scan), self.assertRaises(module.UsageError) as raised:
            module.sync(hw, "unit-a")
        self.assertIn("changed while sync read it", str(raised.exception))

    def test_a_lead_file_over_a_directory_holding_a_local_draft_is_a_conflict_and_records_nothing_for_it(self) -> None:
        (self.hw / "notes").mkdir()
        (self.hw / "notes" / "a.txt").write_text("lead note\n", encoding="utf-8")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (scratch_hw / "notes" / "draft.txt").write_text("my draft\n", encoding="utf-8")
        shutil.rmtree(self.hw / "notes")
        (self.hw / "notes").write_text("the lead now keeps one file\n", encoding="utf-8")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (1, "conflict"), report)
        self.assertIn("notes", report["conflicts"])
        self.assertEqual((scratch_hw / "notes" / "draft.txt").read_text(encoding="utf-8"), "my draft\n")
        baseline = json.loads((scratch_hw.parent / "baseline.json").read_text(encoding="utf-8"))
        self.assertNotIn("notes", baseline["files"])
        self.assertEqual(baseline["files"].get("notes/draft.txt"), None)

    def test_a_lead_directory_over_an_untouched_file_replaces_it(self) -> None:
        (self.hw / "cfg").write_text("one file\n", encoding="utf-8")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (self.hw / "cfg").unlink()
        (self.hw / "cfg").mkdir()
        (self.hw / "cfg" / "x.txt").write_text("now a directory\n", encoding="utf-8")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["added"], report["removed"]), (0, "synced", ["cfg/x.txt"], ["cfg"]), report)
        self.assertEqual((scratch_hw / "cfg" / "x.txt").read_text(encoding="utf-8"), "now a directory\n")
        self.deliver_and_expect_no_proposal("unit-a")

    def test_a_lead_directory_over_a_locally_edited_file_is_a_conflict(self) -> None:
        (self.hw / "cfg").write_text("one file\n", encoding="utf-8")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (scratch_hw / "cfg").write_text("my edit\n", encoding="utf-8")
        (self.hw / "cfg").unlink()
        (self.hw / "cfg").mkdir()
        (self.hw / "cfg" / "x.txt").write_text("now a directory\n", encoding="utf-8")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (1, "conflict"), report)
        self.assertEqual(sorted(report["conflicts"]), ["cfg", "cfg/x.txt"])
        self.assertEqual((scratch_hw / "cfg").read_text(encoding="utf-8"), "my edit\n")

    def test_sync_refuses_a_linked_workspace_directory_and_writes_nothing_through_it(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        work = scratch_hw.parent
        outside = self.outside()
        moved = outside / "moved-workspace"
        shutil.move(str(work), str(moved))
        work.symlink_to(moved)
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# lead edit\n")
        before = tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("symlink", report["error"])
        self.assertEqual(tree(outside), before)

    def test_sync_refuses_a_linked_baseline_and_writes_nothing_through_it(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        baseline = scratch_hw.parent / "baseline.json"
        outside = self.outside()
        (outside / "baseline.json").write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
        baseline.unlink()
        baseline.symlink_to(outside / "baseline.json")
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# lead edit\n")
        before = tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertEqual(tree(outside), before)

    def test_a_directory_swapped_for_a_link_after_the_checks_is_not_written_through(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        (self.hw / "manufacturing" / "lead-note.yaml").write_text("lead\n", encoding="utf-8")
        module = load_scratch()
        real = module.drop_collisions

        def swap(*args):
            shutil.rmtree(scratch_hw / "manufacturing")  # the unit's own process swaps it after the checks
            (scratch_hw / "manufacturing").symlink_to(outside)
            return real(*args)

        before = tree(outside)
        with mock.patch.object(module, "drop_collisions", swap), self.assertRaises(module.UsageError):
            module.sync(self.hw.resolve(), "unit-a")
        self.assertEqual(tree(outside), before)

    def test_sync_installs_nothing_when_a_lead_file_changes_after_the_snapshot(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# first lead edit\n")
        (self.hw / "manufacturing" / "lead-note.yaml").write_text("lead\n", encoding="utf-8")
        module = load_scratch()
        real = module.lead_snapshot

        def snapshot(hw):
            result = real(hw)
            with (hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:  # the lead edits after the snapshot
                stream.write("# second lead edit\n")
            return result

        baseline = scratch_hw.parent / "baseline.json"
        before, baseline_before = tree(scratch_hw), baseline.read_bytes()
        with mock.patch.object(module, "lead_snapshot", snapshot), self.assertRaises(module.UsageError) as raised:
            module.sync(self.hw.resolve(), "unit-a")
        self.assertEqual(raised.exception.finding, "lead-changed-during-read")
        self.assertEqual((tree(scratch_hw), baseline.read_bytes()), (before, baseline_before))

    def test_sync_and_deliver_refuse_while_another_process_holds_the_workspace_lock(self) -> None:
        work = Path(self.init("unit-a", "PRT-0001")["scratch_hw"]).parent
        with (work / "workspace.lock").open("w", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            for command in ("sync", "deliver"):
                with self.subTest(command=command):
                    code, report = self.scratch(command, "--hw", "hw", "--unit", "unit-a")
                    self.assertEqual((code, report["status"], report["finding"]), (2, "error", "workspace-busy"), report)

    def test_a_lead_file_over_an_untouched_directory_replaces_it_and_deliver_proposes_nothing(self) -> None:
        (self.hw / "notes").mkdir()
        (self.hw / "notes" / "a.txt").write_text("lead note\n", encoding="utf-8")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        shutil.rmtree(self.hw / "notes")
        (self.hw / "notes").write_text("the lead now keeps one file\n", encoding="utf-8")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["added"], report["removed"]),
                         (0, "synced", ["notes"], ["notes/a.txt"]), report)
        self.assertEqual((scratch_hw / "notes").read_text(encoding="utf-8"), "the lead now keeps one file\n")
        self.deliver_and_expect_no_proposal("unit-a")

    def test_a_lead_file_over_an_empty_scratch_directory_is_a_conflict(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (scratch_hw / "notes").mkdir()
        (self.hw / "notes").write_text("the lead's file\n", encoding="utf-8")
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["conflicts"]), (1, "conflict", ["notes"]), report)
        self.assertTrue((scratch_hw / "notes").is_dir())
        baseline = json.loads((scratch_hw.parent / "baseline.json").read_text(encoding="utf-8"))
        self.assertNotIn("notes", baseline["files"])

    def test_sync_refuses_an_unreadable_directory_instead_of_reading_its_files_as_retired(self) -> None:
        (self.hw / "notes").mkdir()
        (self.hw / "notes" / "a.txt").write_text("lead note\n", encoding="utf-8")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (self.hw / "notes").chmod(0)
        self.addCleanup((self.hw / "notes").chmod, 0o755)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["finding"]), (2, "error", "unreadable-directory"), report)
        self.assertTrue((scratch_hw / "notes" / "a.txt").is_file())

    def test_sync_and_deliver_refuse_an_unreadable_scratch_directory(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (scratch_hw / "manufacturing").chmod(0)
        self.addCleanup((scratch_hw / "manufacturing").chmod, 0o755)
        before = tree(self.hw)
        for command in ("sync", "deliver"):
            with self.subTest(command=command):
                code, report = self.scratch(command, "--hw", "hw", "--unit", "unit-a")
                self.assertEqual((code, report["finding"]), (2, "unreadable-directory"), report)
        self.assertEqual(tree(self.hw), before)

    def test_a_malformed_or_misshapen_baseline_is_a_named_finding_with_exit_1(self) -> None:
        work = Path(self.init("unit-a", "PRT-0001")["scratch_hw"]).parent
        good = json.loads((work / "baseline.json").read_text(encoding="utf-8"))
        digest = "a" * 64
        variants = {
            "not json": "{ nope",
            "a list": "[]",
            "no hw": {k: v for k, v in good.items() if k != "hw"},
            "parts is a string": {**good, "parts": "PRT-0001"},
            "an empty PN": {**good, "parts": [""]},
            "a PN that is a path": {**good, "parts": ["../x"]},
            "files is a list": {**good, "files": []},
            "a digest that is not a string": {**good, "files": {"product.yaml": 5}},
            "a digest that is not sha256": {**good, "files": {"product.yaml": "short"}},
            "a path that climbs out": {**good, "files": {"../escape": digest}},
            "an absolute path": {**good, "files": {"/etc/passwd": digest}},
        }
        for label, content in variants.items():
            (work / "baseline.json").write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
            for command in ("sync", "deliver"):
                with self.subTest(baseline=label, command=command):
                    code, report = self.scratch(command, "--hw", "hw", "--unit", "unit-a")
                    self.assertEqual((code, report["status"], report["finding"]), (1, "error", "baseline-invalid"), report)

    def test_sync_links_nothing_when_one_cache_destination_is_linked(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        cache = scratch_hw / ".cad" / "cache"
        cache.mkdir(parents=True)
        (cache / "records").symlink_to(outside)
        stored = [f".cad/cache/objects/{'d' * 64}", f".cad/cache/records/{'d' * 64}.json"]
        for rel in stored:
            (self.hw / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.hw / rel).write_text("lead\n", encoding="utf-8")
        before = tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertEqual(tree(outside), before)
        self.assertEqual(list((cache / "objects").glob("*")) if (cache / "objects").exists() else [], [])

    def scratch_state(self, scratch_hw: Path) -> dict[str, str]:
        """The whole workspace except the lock file, so a refusal can prove it changed nothing."""
        return {path: digest for path, digest in tree(scratch_hw.parent).items() if path != "workspace.lock"}

    def test_a_linked_cache_leaf_is_refused_before_any_write(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        leaf = scratch_hw / ".cad" / "cache" / "objects" / ("e" * 64)
        leaf.parent.mkdir(parents=True)
        leaf.symlink_to(outside / "dangling")  # exists() is false for it, so a link over it used to be skipped silently
        stored = [f".cad/cache/objects/{'e' * 64}", f".cad/cache/records/{'f' * 64}.json"]
        for rel in stored:
            (self.hw / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.hw / rel).write_text("lead\n", encoding="utf-8")
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# lead edit\n")
        before, outside_before = self.scratch_state(scratch_hw), tree(outside)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("symlink", report["error"])
        self.assertEqual((self.scratch_state(scratch_hw), tree(outside)), (before, outside_before))

    def test_a_linked_ancestor_of_the_leads_cache_is_refused_before_any_write(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        outside = self.outside()
        stored = outside / "cache" / "objects" / ("a" * 64)
        stored.parent.mkdir(parents=True)
        stored.write_text("elsewhere\n", encoding="utf-8")
        (self.hw / ".cad").mkdir()
        (self.hw / ".cad" / "cache").symlink_to(outside / "cache")  # below the skipped .cad/, so only seeding meets it
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# lead edit\n")
        before = self.scratch_state(scratch_hw)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (2, "error"), report)
        self.assertIn("symlink", report["error"])
        self.assertEqual(self.scratch_state(scratch_hw), before)

    def test_a_retained_empty_child_keeps_its_parents_clean_files(self) -> None:
        (self.hw / "notes").mkdir()
        (self.hw / "notes" / "a.txt").write_text("lead note\n", encoding="utf-8")
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        (scratch_hw / "notes" / "drafts").mkdir()  # the unit's own empty directory
        shutil.rmtree(self.hw / "notes")
        (self.hw / "notes").write_text("the lead now keeps one file\n", encoding="utf-8")
        before = self.scratch_state(scratch_hw)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"], report["conflicts"]), (1, "conflict", ["notes", "notes/a.txt"]), report)
        self.assertEqual((report["added"], report["removed"]), ([], []))
        self.assertEqual((scratch_hw / "notes" / "a.txt").read_text(encoding="utf-8"), "lead note\n")
        self.assertTrue((scratch_hw / "notes" / "drafts").is_dir())
        self.assertEqual(self.scratch_state(scratch_hw), before)

    def crash(self, module, unit: str, point: str) -> None:
        """Run a sync in this process and kill it at `point`: SystemExit is not an OSError, so nothing cleans up after it."""
        real_rename, real_replace, calls = module.os.rename, module.os.replace, []

        def rename(*args, **kwargs):
            calls.append(args)
            if point == "before the swap's first rename" and len(calls) == 1 or point == "after the old tree moved aside" and len(calls) == 2:
                raise SystemExit("killed")
            return real_rename(*args, **kwargs)

        def replace(*args, src_dir_fd=None, dst_dir_fd=None, **kwargs):
            if point == "after the new tree is in place" and src_dir_fd != dst_dir_fd:  # the baseline moving out of the staging directory
                raise SystemExit("killed")
            return real_replace(*args, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, **kwargs)

        def killed_write_baseline(*args, **kwargs):
            raise SystemExit("killed")

        patches = [mock.patch.object(module.os, "rename", rename), mock.patch.object(module.os, "replace", replace)]
        if point == "after the changes, before the baseline":
            patches = [mock.patch.object(module, "write_baseline", killed_write_baseline)]
        with contextlib.ExitStack() as stack, self.assertRaises(SystemExit):
            for patch in patches:
                stack.enter_context(patch)
            module.sync(self.hw.resolve(), unit)

    def test_a_crash_at_any_step_followed_by_a_lead_revert_proposes_nothing_stale(self) -> None:
        original = (self.hw / "interfaces.yaml").read_bytes()
        points = ("after the changes, before the baseline", "before the swap's first rename",
                  "after the old tree moved aside", "after the new tree is in place")
        for number, point in enumerate(points):
            with self.subTest(point=point):
                unit = f"unit-{number}"
                scratch_hw = Path(self.init(unit, "PRT-0001")["scratch_hw"])
                with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
                    stream.write("# lead edit that is reverted\n")
                self.crash(load_scratch(), unit, point)
                (self.hw / "interfaces.yaml").write_bytes(original)
                code, report = self.sync(unit)
                self.assertEqual((code, report["status"], report["added"], report["modified"], report["removed"]),
                                 (0, "synced", [], [], []), report)
                self.assertEqual([p.name for p in scratch_hw.parent.glob("sync-*")], [])
                self.assertEqual((scratch_hw / "interfaces.yaml").read_bytes(), original)
                self.deliver_and_expect_no_proposal(unit)

    def test_a_failure_after_the_preflight_leaves_the_workspace_byte_identical(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        self.assertEqual(self.sync("unit-a")[0], 0)  # nothing to sync yet; this run only creates the lock file
        with (self.hw / "interfaces.yaml").open("a", encoding="utf-8") as stream:
            stream.write("# lead edit\n")
        (self.hw / "manufacturing" / "lead-note.yaml").write_text("lead\n", encoding="utf-8")
        module = load_scratch()
        real, calls = module.install_synced, []

        def install(*args):
            calls.append(args[1])
            if len(calls) == 2:
                raise OSError(errno.EIO, "disk trouble")
            return real(*args)

        before = self.scratch_state(scratch_hw)
        with mock.patch.object(module, "install_synced", install), self.assertRaises(module.Finding) as raised:
            module.sync(self.hw.resolve(), "unit-a")
        self.assertEqual(raised.exception.finding, "sync-failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.scratch_state(scratch_hw), before)
        self.assertEqual([p.name for p in scratch_hw.parent.glob("sync-*")], [])
        code, report = self.sync("unit-a")  # and a retry works
        self.assertEqual((code, report["status"]), (0, "synced"), report)
        self.deliver_and_expect_no_proposal("unit-a")

    def test_a_stranded_staging_directory_is_removed_and_an_aside_tree_is_put_back(self) -> None:
        work = Path(self.init("unit-a", "PRT-0001")["scratch_hw"]).parent
        (work / "sync-deadbeef" / "hw").mkdir(parents=True)  # a sync killed before its swap
        self.deliver_and_expect_no_proposal("unit-a")
        self.assertFalse((work / "sync-deadbeef").exists())
        (work / "sync-cafef00d").mkdir()  # a sync killed after it moved the old tree aside
        (work / "hw").rename(work / "sync-cafef00d" / "old-hw")
        (work / "sync-cafef00d" / "hw").mkdir()
        before = tree(self.hw)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (0, "synced"), report)
        self.assertTrue((work / "hw" / "parts" / "PRT-0001" / "part.yaml").is_file())
        self.assertFalse((work / "sync-cafef00d").exists())
        self.assertEqual(tree(self.hw), before)

    def test_a_part_the_unit_owns_is_never_synced(self) -> None:
        scratch_hw = Path(self.init("unit-a", "PRT-0001")["scratch_hw"])
        record = self.hw / "parts" / "PRT-0001" / "part.yaml"
        record.write_text(record.read_text(encoding="utf-8").replace("mass_target: null", "mass_target: 10"), encoding="utf-8")
        scratch_before = tree(scratch_hw)
        code, report = self.sync("unit-a")
        self.assertEqual((code, report["status"]), (1, "conflict"), report)
        self.assertEqual(report["conflicts"], ["parts/PRT-0001/part.yaml"])
        self.assertEqual(tree(scratch_hw), scratch_before)


class JsonChildren(unittest.TestCase):
    def test_run_tool_parses_a_child_that_is_asked_for_json(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scratch_module = load_scratch()
        done = subprocess.CompletedProcess([], 0, stdout='{"schema": "tool-result/1", "status": "pass", "findings": [], "payload": {"findings": []}}', stderr="")
        with mock.patch.dict("os.environ", {"CAD_OUTPUT": "compact"}), \
                mock.patch.object(scratch_module.subprocess, "run", return_value=done) as run:
            result = scratch_module.run_tool([str(scratch_module.CAD_MODEL)], Path(tmp.name) / "log")
        self.assertEqual(result, (0, {"findings": []}))
        self.assertEqual(run.call_args.kwargs["env"]["CAD_OUTPUT"], "json")


class LampExample(ScratchCase):
    def setUp(self) -> None:
        super().setUp()
        self.use_lamp_example()

    def test_lamp_example_copies_as_a_separate_scratch_project(self) -> None:
        report = self.init("part-base_sub", "LMP-0001")
        scratch_hw = Path(report["scratch_hw"])
        self.assertTrue((scratch_hw / "parts" / "LMP-0001" / "model.py").is_file())
        self.assertEqual(json.loads((scratch_hw.parent / "baseline.json").read_text(encoding="utf-8"))["parts"],
                         ["LMP-0001"])
        self.assertEqual(sorted(path.name for path in (scratch_hw / "parts").iterdir()),
                         [f"LMP-{number:04d}" for number in range(1, 12)])


    @unittest.skipUnless(FREECAD_PRESENT, "lamp build needs FreeCAD 1.1.4 bundled Osifont")
    def test_build_checks_the_scratch_copy_and_never_touches_hw_versions(self) -> None:
        before = tree(self.hw)
        self.init("part-base_sub", "LMP-0001")
        code, report = self.scratch("build", "--hw", "hw", "--unit", "part-base_sub", "--fidelity", "F2",
                                    "--kernel", "cadquery", python=False, timeout=1800)
        self.assertEqual((code, report["status"]), (0, "pass"), json.dumps(report, indent=2))
        self.assertEqual(report["label"], "v1")
        scratch_hw = (self.project / ".cad-work" / "part-base_sub" / "hw").resolve()
        self.assertEqual(Path(report["version_dir"]), scratch_hw / "versions" / "v1")
        self.assertTrue((scratch_hw / "versions" / "v1" / "parts" / "LMP-0001.step").is_file())
        self.assertEqual(report["check"]["exit"], 0)
        self.assertTrue(Path(report["check"]["json"]).is_file() and Path(report["form"]["json"]).is_file())
        for tool in ("run", "check", "form"):
            self.assertTrue(Path(report[tool]["log"]).is_file(), tool)
        self.assertEqual(report["form"]["own_findings"], [])
        self.assertEqual(tree(self.hw), before)


if __name__ == "__main__":
    unittest.main()
