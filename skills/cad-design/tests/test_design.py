#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-design tests: reference lamp-arm models in both kernels, the block-like detector, and the wall scan.

Run: python3 -m unittest discover -s cad/skills/cad-design/tests -v
Needs `uv`; the first run creates the CadQuery and build123d environments, and a cold
OCP import can take minutes, so the builds have no short timeout.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
EXAMPLE = SKILL.parent.parent / "examples" / "lamp-arm"
TEMPLATE_HW = EXAMPLE / "hw"
FIXTURES = SKILL.parent / "cad-motion" / "tests" / "fixtures" / "build_fixtures.py"
DEFECTS = HERE / "fixtures" / "defects"
KERNELS = ("cadquery", "build123d")
TEMPLATE_PARTS = sorted(p.name for p in (EXAMPLE / "hw" / "parts").iterdir() if (p / "part.yaml").is_file())
sys.path.insert(0, str(SKILL.parent / "cad-drawings" / "scripts"))
import freecad_runtime  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import result_payload  # noqa: E402

FREECADCMD = freecad_runtime.find_freecadcmd()
FREECAD_PRESENT = FREECADCMD is not None and FREECADCMD.is_file() and os.access(FREECADCMD, os.X_OK)

_spec = importlib.util.spec_from_file_location("cad_design_form_check", SCRIPTS / "form_check.py")
assert _spec and _spec.loader
FORM_CHECK = importlib.util.module_from_spec(_spec)  # its OCP imports are deferred to the functions that need them
_spec.loader.exec_module(FORM_CHECK)

MADE_PARTS = ["LMP-0001", "LMP-0002", "LMP-0003", "LMP-0004"]


def run_json(command: list[str], parse=json.loads) -> tuple[int, dict]:
    """`command`'s exit code and its stdout parsed by `parse` (form_check's is enveloped: result_payload)."""
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return result.returncode, parse(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"{command[:4]} printed no JSON (exit {result.returncode}):\n{result.stderr[-3000:]}") from error


def form_check(parts_dir: Path, fidelity: str, hw: Path = TEMPLATE_HW) -> tuple[int, dict]:
    return run_json(["uv", "run", "--script", str(SCRIPTS / "form_check.py"), "--hw", str(hw),
                     "--parts-dir", str(parts_dir), "--fidelity", fidelity], parse=result_payload)


FDM_MADE = "process: fdm\nmake_buy: make\n"


def build_parts(root: Path, parts: dict[str, tuple[str, str]]) -> tuple[Path, Path]:
    """Build one STEP per {PN: (part.yaml lines after PN, CadQuery expression)} with cad-design's product runner."""
    hw, models, parts_dir = root / "hw", root / "models", root / "parts"
    (hw / "parts").mkdir(parents=True)
    models.mkdir()
    (hw / "assembly.yaml").write_text("joints: []\n", encoding="utf-8")
    for pn, (record, expression) in parts.items():
        (hw / "parts" / pn).mkdir()
        (hw / "parts" / pn / "part.yaml").write_text(f"PN: {pn}\n{record}", encoding="utf-8")
        (models / f"{pn}.py").write_text('import cadquery as cq\nKERNEL = "cadquery"\nPARAMS = {}\n\n'
                                         f'def build(params):\n    return {expression}\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPTS / "build_reference.py"), "--kernel", "cadquery",
                             "--models", str(models), "--hw", str(hw), "--out", str(parts_dir)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"build_reference.py failed: {result.stderr[-2000:]}")
    return hw, parts_dir


def box_part(thickness: float, form_class: str | None = None) -> dict[str, tuple[str, str]]:
    record = FDM_MADE + (f"form_class: {form_class}\n" if form_class else "")
    return {"TST-0301": (record, f"cq.Workplane().box(10, 10, {thickness})")}


@unittest.skipUnless(FREECAD_PRESENT, "reference lamp tests need FreeCAD 1.1.4's bundled Osifont")
class ReferenceModelTests(unittest.TestCase):
    """Both kernels' reference parts: build, B-rep, contract parity, joints, and form."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.builds, cls.checks = {}, {}
        for kernel in KERNELS:
            out = root / kernel
            code, report = run_json([sys.executable, str(SCRIPTS / "build_reference.py"), "--kernel", kernel,
                                     "--out", str(out)])
            cls.builds[kernel] = (code, report)
            cls.checks[kernel] = form_check(out, "F3")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_template_part_builds_in_both_kernels(self) -> None:
        for kernel in KERNELS:
            code, report = self.builds[kernel]
            with self.subTest(kernel=kernel):
                self.assertEqual((code, report["errors"]), (0, []))
                self.assertEqual(sorted(report["parts"]), TEMPLATE_PARTS)

    def test_step_products_are_named_pn_then_title(self) -> None:
        for kernel in KERNELS:
            for pn, row in self.checks[kernel][1]["parts"].items():
                with self.subTest(kernel=kernel, part=pn):
                    self.assertRegex(row["name"], rf"^{pn} \S")

    def test_brep_is_one_valid_solid_per_part(self) -> None:
        for kernel in KERNELS:
            for pn, row in self.checks[kernel][1]["parts"].items():
                with self.subTest(kernel=kernel, part=pn):
                    self.assertTrue(row["valid"])
                    self.assertTrue(row["kernel_valid"])
                    self.assertLessEqual(row["readback_rel_error"], 1e-6)
                    self.assertEqual(row["solids"], 1)

    def test_params_key_sets_are_equal_across_kernels(self) -> None:
        cq, bd = (self.builds[k][1]["parts"] for k in KERNELS)
        for pn in TEMPLATE_PARTS:
            with self.subTest(part=pn):
                self.assertEqual(cq[pn]["params_keys"], bd[pn]["params_keys"])

    def test_volumes_agree_within_half_a_percent(self) -> None:
        cq, bd = (self.checks[k][1]["parts"] for k in KERNELS)
        for pn in TEMPLATE_PARTS:
            with self.subTest(part=pn):
                a, b = cq[pn]["volume_mm3"], bd[pn]["volume_mm3"]
                self.assertGreater(a, 0.0)
                self.assertLessEqual(abs(a - b) / a, 0.005, f"cadquery {a} mm3 vs build123d {b} mm3")

    def test_joint_bores_sit_on_template_origins(self) -> None:
        for kernel in KERNELS:
            joints = self.checks[kernel][1]["joints"]
            self.assertEqual(sorted(joints), [f"J-{n:03d}" for n in range(1, 13)])
            for jid, row in joints.items():
                for pn, found in row["parts"].items():
                    with self.subTest(kernel=kernel, joint=jid, part=pn):
                        self.assertIsNotNone(found)
                        self.assertLessEqual(found["offset_mm"], 0.05)

    def test_no_reference_part_is_block_like_or_breaks_a_process_rule(self) -> None:
        for kernel in KERNELS:
            code, report = self.checks[kernel]
            with self.subTest(kernel=kernel):
                self.assertEqual((code, report["findings"]), (0, []))


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the CAD shape fixtures")
class BlockShapeTests(unittest.TestCase):
    """Block-like detection distinguishes a solid placeholder from a planar skeleton."""

    def test_box_fails_and_open_frame_passes_without_blends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            models.mkdir()
            (models / "TST-0301.py").write_text(
                'import cadquery as cq\nKERNEL = "cadquery"\nPARAMS = {}\n\n'
                'def build(params):\n    return cq.Workplane().box(100, 100, 20)\n', encoding="utf-8")
            (models / "TST-0302.py").write_text(
                'import cadquery as cq\nKERNEL = "cadquery"\nPARAMS = {}\n\n'
                'def build(params):\n'
                '    outer = cq.Workplane().box(100, 100, 20)\n'
                '    inner = cq.Workplane().box(90, 90, 22)\n'
                '    return outer.cut(inner)\n', encoding="utf-8")
            (models / "TST-0303.py").write_text(
                'import cadquery as cq\nKERNEL = "cadquery"\nPARAMS = {}\n\n'
                'def build(params):\n    return cq.Workplane().cylinder(100, 10)\n', encoding="utf-8")
            out = Path(tmp) / "out"
            code, built = run_json([sys.executable, str(SCRIPTS / "build_reference.py"), "--kernel", "cadquery",
                                    "--models", str(models), "--out", str(out)])
            self.assertEqual((code, built["errors"]), (0, []))
            code, report = form_check(out, "F2")

        # The lamp's joints name parts these stand-ins replace: those joint findings block; block_like never does.
        self.assertEqual(code, 1)
        self.assertEqual({f["check"] for f in report["findings"] if f["blocking"]}, {"joint_bore_missing"})
        by_part = {row["part"]: row for row in report["findings"] if row["check"] == "block_like"}
        self.assertEqual(sorted(by_part), ["TST-0301", "TST-0303"])
        self.assertEqual({row["blocking"] for row in by_part.values()}, {False})
        box = report["parts"]["TST-0301"]
        frame = report["parts"]["TST-0302"]
        pin = report["parts"]["TST-0303"]
        self.assertGreaterEqual(box["axis_planar_fraction"], 0.80)
        self.assertGreaterEqual(box["obb_fill_fraction"], 0.20)
        self.assertEqual(box["blend_faces"] + box["freeform_faces"], 0)
        self.assertEqual(frame["axis_planar_fraction"], 1.0)
        self.assertLess(frame["obb_fill_fraction"], 0.20)
        self.assertGreaterEqual(frame["obb_thickness_fraction"], 0.10)
        self.assertEqual(frame["blend_faces"] + frame["freeform_faces"], 0)
        self.assertLess(pin["axis_planar_fraction"], 0.80)
        self.assertEqual(pin["blend_faces"] + pin["freeform_faces"], 0)
        self.assertIn("TST-0303", by_part)

    def test_non_solid_block_is_exempt_and_wall_override_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hw, parts_dir = build_parts(Path(tmp), box_part(1.1, "frame"))
            (hw / "product.yaml").write_text("id: fixture\nmanufacturing:\n  min_wall_mm: {fdm: 1.0}\n", encoding="utf-8")
            code, report = form_check(parts_dir, "F2", hw)
            self.assertEqual(code, 0, report["findings"])
            self.assertEqual(report["parts"]["TST-0301"]["form_class"], "frame")
            self.assertEqual(report["parts"]["TST-0301"]["min_wall"]["thickness_mm"], 1.1)
            (hw / "product.yaml").write_text("id: fixture\nmanufacturing:\n  min_wall_mm: {fdm: 1.15}\n", encoding="utf-8")
            code, report = form_check(parts_dir, "F2", hw)
            wall = next(f for f in report["findings"] if f["check"] == "min_wall")
            self.assertEqual((code, wall["part"], wall["min_mm"], wall["thickness_mm"], wall["blocking"]),
                             (0, "TST-0301", 1.15, 1.1, False))
            self.assertEqual(len(wall["location_mm"]), 3)

    def test_solid_box_defaults_to_block_like_and_an_unknown_wall_limit_without_a_capability_profile(self) -> None:
        """A part that names no capability profile has no guideline scope, as in the process checks: its minimum wall is
        unknown (no scan, no invented floor), and a min_wall_limit_unknown diagnostic says why."""
        with tempfile.TemporaryDirectory() as tmp:
            hw, parts_dir = build_parts(Path(tmp), {**box_part(1.1), "TST-0302": (FDM_MADE, "cq.Workplane().box(10, 10, 0.7)")})
            code, report = form_check(parts_dir, "F2", hw)
            block = next(f for f in report["findings"] if f["check"] == "block_like" and f["part"] == "TST-0301")
            unknown = {f["part"]: f for f in report["findings"] if f["check"] == "min_wall_limit_unknown"}
            self.assertEqual((code, block["blocking"], report["status"]), (0, False, "pass"))
            self.assertEqual([f for f in report["findings"] if f["check"] == "min_wall"], [])
            self.assertEqual(sorted(unknown), ["TST-0301", "TST-0302"])
            self.assertEqual({f["blocking"] for f in unknown.values()}, {False})
            self.assertTrue(unknown["TST-0302"]["reasons"])
            limit = report["parts"]["TST-0301"]["min_wall"]["limit"]
            self.assertEqual((limit["required_mm"], limit["status"]), (None, "unknown"))


PLATE_08 = "cq.Workplane().box(10, 10, 0.8)"
BORED_BLOCK = "cq.Workplane().box(20, 20, 10).faces('>Z').workplane().center({}, {}).hole(10)"
FILLETED_SHELL = "cq.Workplane().box(30, 20, 15).edges('|Z or <Z').fillet(4).faces('>Z').shell(-{})"
# A Ø10 bore `web` mm from one side of a 20 mm block, toward +X (the bore's seam), -X, +Y and -Y. The web is
# thinnest at one spot and thickens away from it, by about s²/2r: 1.15 mm is under 1.2 mm only within ±0.64 mm.
WEBS = {f"TST-04{base + i:02d}": (web, direction) for web, base in ((0.8, 1), (1.1, 31), (1.15, 35))
        for i, direction in enumerate(((1, 0), (-1, 0), (0, 1), (0, -1)))}
WALL_PARTS = {
    **{pn: (FDM_MADE, BORED_BLOCK.format(round(dx * (5 - web), 6), round(dy * (5 - web), 6)))
       for pn, (web, (dx, dy)) in WEBS.items()},
    # A 0.8 mm rib 2.5 mm tall on a 3 mm plate.
    "TST-0405": (FDM_MADE, "cq.Workplane().box(20, 20, 3).faces('>Z').workplane().rect(15, 0.8).extrude(2.5)"),
    # A lug whose Ø6.4 bore leaves a 0.8 mm ring in its R4 end.
    "TST-0406": (FDM_MADE, "cq.Workplane().box(20, 8, 3).translate((10, 0, 0)).union(cq.Workplane().cylinder(3, 4))"
                           ".cut(cq.Workplane().cylinder(3, 3.2))"),
    # A 2 mm slot 4.2 mm deep in a 5 mm block: a 0.8 mm floor.
    "TST-0407": (FDM_MADE, "cq.Workplane().box(20, 10, 5).faces('>Z').workplane().rect(16, 2).cutBlind(-4.2)"),
    "TST-0408": (FDM_MADE + "form_class: shell\n", FILLETED_SHELL.format(0.8)),
    "TST-0409": (FDM_MADE + "form_class: shell\n", FILLETED_SHELL.format(1.6)),
    "TST-0410": (FDM_MADE + "form_class: plate\n", "cq.Workplane().box(10, 10, 1.2)"),
    # A 90-degree cone: every exit is 45 degrees off antiparallel, so no sample measures a wall.
    "TST-0411": (FDM_MADE, "cq.Workplane().add(cq.Solid.makeCone(5, 0, 5))"),
    "TST-0412": ("process: purchased\nmake_buy: buy\nform_class: plate\n", PLATE_08),
    "TST-0413": ("process: fdm\nmake_buy: buy\nform_class: plate\n", PLATE_08),
    "TST-0414": ("process: cnc\nmake_buy: make\nform_class: plate\n", PLATE_08),
    "TST-0419": ("process: fdm\nmake_buy: existing\nform_class: plate\n", PLATE_08),
    # A solid cube: block-like and wall-scanned if the existing skip did not apply.
    "TST-0420": ("process: fdm\nmake_buy: existing\n", "cq.Workplane().box(20, 20, 20)"),
    "TST-0415": (FDM_MADE + "form_class: plate\n",
                 "cq.Workplane().box(110, 110, 3).faces('>Z').workplane().rarray(10, 10, 10, 10).hole(4)"),
    # A prism with a 30-degree edge: its tip is a wedge, not a wall; the end caps are 10 mm apart.
    "TST-0416": (FDM_MADE, "cq.Workplane().polyline([(0, 0), (20, 0), (20, 11.547005)]).close().extrude(10)"),
    # Two 0.6 mm grooves leave a 1.0 mm land: relief on a 4 mm plate, not a wall.
    "TST-0417": (FDM_MADE, "cq.Workplane().box(20, 20, 4).faces('>Z').workplane()"
                           ".pushPoints([(-1.5, 0), (1.5, 0)]).rect(2, 12).cutBlind(-0.6)"),
    # A Ø4 screw boss with a Ø2 hole: a 1.0 mm wall curved tighter than the persistence step.
    "TST-0418": (FDM_MADE, "cq.Workplane().box(20, 20, 3).faces('>Z').workplane().circle(2).extrude(6)"
                           ".faces('>Z').workplane().hole(2, 6)"),
}
HUNDRED_HOLES_SECONDS = 15.0  # about 1 s on an M2; the per-ray reload it replaces took minutes at this density


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the CAD shape fixtures")
class WallScanTests(unittest.TestCase):
    """The F2 minimum-wall scan finds thin features without flagging walls that are not thin."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        hw, parts_dir = build_parts(Path(cls.tmp.name), WALL_PARTS)
        # These parts name no capability profile, so their resolved fdm minimum is unknown. The scan is tested at the
        # 1.2 mm a 0.4 mm nozzle resolves to, given here as the project's minimum.
        (hw / "product.yaml").write_text("id: fixture\nmanufacturing:\n  min_wall_mm: {fdm: 1.2}\n", encoding="utf-8")
        cls.code, cls.report = form_check(parts_dir, "F2", hw)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def wall(self, pn: str) -> dict:
        return self.report["parts"][pn]["min_wall"]

    def wall_findings(self, pn: str) -> list[dict]:
        return [f for f in self.report["findings"] if f["part"] == pn and f["check"].startswith("min_wall")]

    def assert_thin(self, pn: str, thickness: float) -> list[float]:
        """One min_wall finding, at most 0.02 mm above the true thickness; returns its location."""
        findings = self.wall_findings(pn)
        self.assertEqual([f["check"] for f in findings], ["min_wall"], pn)
        self.assertGreaterEqual(findings[0]["thickness_mm"], thickness - 1e-6, pn)
        self.assertLess(findings[0]["thickness_mm"], thickness + 0.02, pn)
        self.assertEqual(findings[0]["min_mm"], 1.2)
        return findings[0]["location_mm"]

    def assert_passes(self, pn: str, thickness: float) -> None:
        self.assertEqual(self.wall_findings(pn), [], pn)
        self.assertAlmostEqual(self.wall(pn)["thickness_mm"], thickness, places=6, msg=pn)

    def test_bore_webs_are_found_whichever_way_the_seam_faces(self) -> None:
        for pn, (web, (dx, dy)) in WEBS.items():
            with self.subTest(part=pn, web=web):
                location = self.assert_thin(pn, web)
                self.assertLess(math.dist(location[:2], (dx * (10 - web / 2), dy * (10 - web / 2))), 1.0)

    def test_narrow_faces_of_rib_lug_ring_floor_and_boss_are_sampled(self) -> None:
        x, y, z = self.assert_thin("TST-0405", 0.8)
        self.assertTrue(abs(y) <= 0.4 + 1e-6 and 1.5 - 1e-6 <= z <= 4.0 + 1e-6, (x, y, z))
        x, y, _ = self.assert_thin("TST-0406", 0.8)
        self.assertTrue(3.2 - 1e-6 <= math.hypot(x, y) <= 4.0 + 1e-6, (x, y))
        x, y, _ = self.assert_thin("TST-0407", 0.8)
        self.assertTrue(abs(x) <= 8.0 and abs(y) <= 1.0, (x, y))
        x, y, _ = self.assert_thin("TST-0418", 1.0)
        self.assertTrue(1.0 - 1e-6 <= math.hypot(x, y) <= 2.0 + 1e-6, (x, y))

    def test_filleted_shell_flags_at_0_8_and_passes_at_1_6(self) -> None:
        self.assert_thin("TST-0408", 0.8)
        self.assert_passes("TST-0409", 1.6)

    def test_wall_at_exactly_the_minimum_passes(self) -> None:
        self.assert_passes("TST-0410", 1.2)
        self.assertEqual([f for f in self.report["findings"] if f["part"] == "TST-0410"], [])

    def test_acute_edge_wedge_and_relief_are_not_walls(self) -> None:
        wedge, relief = self.wall("TST-0416"), self.wall("TST-0417")
        self.assert_passes("TST-0416", 10.0)  # every tip chord is rejected, so the end caps set the minimum
        self.assertGreater(wedge["tapered"], 0)
        self.assert_passes("TST-0417", 3.4)  # the 1.0 mm land is 0.6 mm tall
        self.assertGreater(relief["shallow"], 0)
        self.assertAlmostEqual(relief["shallow_thickness_mm"], 1.0, places=6)
        self.assertTrue(1.4 - 1e-6 <= relief["shallow_location_mm"][2] <= 2.0 + 1e-6, relief["shallow_location_mm"])

    def test_part_without_a_measured_wall_is_a_finding(self) -> None:
        wall = self.wall("TST-0411")
        self.assertEqual((wall["thickness_mm"], wall["measured"], wall["faces_sampled"]), (None, 0, 2))
        self.assertGreater(wall["samples"], 0)
        self.assertEqual(wall["tapered"], wall["samples"])  # the coverage shows why: every exit is a taper
        self.assertEqual(self.wall_findings("TST-0411"), [{"check": "min_wall_unmeasured", "part": "TST-0411",
                                                            "min_mm": 1.2, "faces": 2, "faces_sampled": 2,
                                                            "samples": wall["samples"], "blocking": False}])

    def test_bought_parts_and_processes_without_a_minimum_are_not_scanned(self) -> None:
        for pn in ("TST-0412", "TST-0413", "TST-0414"):
            with self.subTest(part=pn):
                self.assertEqual(self.wall(pn), {"required_mm": None})
                self.assertEqual([f for f in self.report["findings"] if f["part"] == pn], [])

    def test_existing_equipment_has_no_form_check(self) -> None:
        for pn in ("TST-0419", "TST-0420"):
            with self.subTest(part=pn):
                self.assertEqual(self.report["parts"][pn]["process"], "existing")
                self.assertEqual(self.wall(pn), {"required_mm": None})
                self.assertEqual([f for f in self.report["findings"] if f["part"] == pn], [])

    def test_plate_with_100_holes_passes_within_its_time_budget(self) -> None:
        wall = self.wall("TST-0415")
        self.assert_passes("TST-0415", 3.0)
        self.assertEqual((wall["faces"], wall["faces_sampled"]), (106, 106))
        self.assertLess(wall["seconds"], HUNDRED_HOLES_SECONDS)

    def test_every_scan_reports_its_coverage(self) -> None:
        for pn, row in self.report["parts"].items():
            wall = row["min_wall"]
            if wall["required_mm"] is None:
                continue
            with self.subTest(part=pn):
                self.assertEqual(wall["spacing_mm"], 0.6)
                self.assertTrue(0 < wall["faces_sampled"] <= wall["faces"], wall)
                self.assertTrue(0 <= wall["measured"] + wall["shallow"] + wall["tapered"] <= wall["samples"], wall)
                self.assertEqual(wall["shallow_thickness_mm"] is None, wall["shallow"] == 0, wall)
                self.assertEqual(wall["shallow_location_mm"] is None, wall["shallow"] == 0, wall)
                self.assertGreaterEqual(wall["seconds"], 0.0)


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the CAD shape fixtures")
class ExistingJointTests(unittest.TestCase):
    """A joint endpoint on existing equipment mates through whichever cylinder it has, a bore or a shank."""

    def test_existing_shaft_shank_in_a_product_bore_passes_and_a_missing_cylinder_fails(self) -> None:
        existing = "process: fdm\nmake_buy: existing\n"
        with tempfile.TemporaryDirectory() as tmp:
            hw, parts_dir = build_parts(Path(tmp), {
                "TST-0501": (FDM_MADE + "form_class: frame\n", "cq.Workplane().box(20, 20, 10).faces('>Z').workplane().hole(10)"),
                "TST-0502": (existing, "cq.Workplane().cylinder(30, 5)"),
                "TST-0503": (existing, "cq.Workplane().box(20, 20, 20)")})
            joint = "  - {{id: {}, type: revolute, parent: TST-0501, child: {}, axis: [0, 0, 1], origin: [0, 0, 0], " \
                    "min: -10, max: 10, nominal: 0}}\n"
            (hw / "assembly.yaml").write_text("joints:\n" + joint.format("J-001", "TST-0502")
                                              + joint.format("J-002", "TST-0503"), encoding="utf-8")
            code, report = form_check(parts_dir, "F2", hw)
        missing = [(f["joint"], f["part"]) for f in report["findings"] if f["check"] == "joint_bore_missing"]
        self.assertEqual((code, missing), (1, [("J-002", "TST-0503")]))
        self.assertEqual(report["joints"]["J-001"]["parts"]["TST-0502"]["kind"], "shank")


BOUGHT = "process: purchased\nmake_buy: buy\n"
SHAFT = "cq.Workplane().cylinder(30, 5)"
BORED = "cq.Workplane().box(20, 20, 10).faces('>Z').workplane().hole(10)"
PAD = "cq.Workplane().box(20, 10, 6)"  # 6 mm tall: on the deck (top z = 5) when its centre is at z = 8
JOINT_GEOMETRY_PARTS = {
    "TST-0601": (FDM_MADE, SHAFT),
    "TST-0602": (BOUGHT, BORED),
    "TST-0603": (FDM_MADE + "form_class: frame\n", BORED),
    "TST-0604": (BOUGHT, SHAFT),
    "TST-0605": (FDM_MADE, "cq.Workplane().box(40, 40, 10)"),
    "TST-0606": (FDM_MADE + "model_frame: part_local\n", PAD),  # placed on the deck by its occurrence
    "TST-0607": (FDM_MADE, PAD + ".translate((0, 0, 8.5))"),  # 0.5 mm above the deck
    "TST-0608": (FDM_MADE, PAD + ".translate((0, 0, 8))"),
    "TST-0609": (FDM_MADE, SHAFT + ".translate((0.2, 0, 0))"),
    "TST-0610": (BOUGHT, "cq.Workplane().cylinder(10, 8).faces('>Z').workplane().hole(6)"),  # a roller: Ø16 over a Ø6 bore
    "TST-0611": (FDM_MADE, "cq.Workplane().cylinder(20, 3)"),  # its stub axle
    # An R3 edge fillet whose axis is the joint axis at (7, 7), beside a Ø6 bore on the same axis.
    "TST-0615": (FDM_MADE, "cq.Workplane().box(20, 20, 10).edges('|Z and >X and >Y').fillet(3)"),
    "TST-0616": (BOUGHT, "cq.Workplane().box(20, 20, 10).cut(cq.Workplane().cylinder(12, 3).translate((7, 7, 0)))"),
    # A half-bore journal: a Ø10 bore through the block's edge leaves a 180-degree concave face.
    "TST-0617": (FDM_MADE, "cq.Workplane().box(20, 20, 10).translate((10, 0, 0)).cut(cq.Workplane().cylinder(12, 5))"),
    # A D-shaft: a flat 4 mm off the axis leaves a 286-degree convex face of radius 5.
    "TST-0618": (FDM_MADE, SHAFT + ".cut(cq.Workplane().box(10, 10, 30).translate((9, 0, 0)))"),
    # A 20 mm pad tilted 0.003 degrees about y, 0.02 mm above the deck at its centre: within tolerance everywhere.
    "TST-0614": (FDM_MADE, "cq.Workplane().box(20, 20, 6).rotate((0, 0, 0), (0, 1, 0), 0.003).translate((0, 0, 8.02))"),
    # A 200 mm pad tilted 0.005 degrees, 0.045 mm above its 220 mm deck at its centre: 0.0537 mm at one end.
    "TST-0612": (FDM_MADE, "cq.Workplane().box(220, 20, 10)"),
    "TST-0613": (FDM_MADE, "cq.Workplane().box(200, 10, 6).rotate((0, 0, 0), (0, 1, 0), 0.005).translate((0, 0, 8.045))"),
    # A deck whose top face has a 10 mm pocket across x = 0: the pad on it touches two islands, 5 mm either side.
    "TST-0620": (FDM_MADE, "cq.Workplane().box(40, 40, 10).cut(cq.Workplane().box(10, 12, 4).translate((0, 0, 4)))"),
    # The pad with a Ø4 hole through it on z, like a head's bore opening over the deck.
    "TST-0621": (FDM_MADE, "cq.Workplane().box(20, 10, 6).faces('>Z').workplane().hole(4).translate((0, 0, 8))"),
}
JOINT_GEOMETRY_ASSEMBLY = """\
occurrences:
- id: pad
  PN: TST-0606
  placement:
    translation_mm: [3, 2, 8]
    rotation: {axis: [0, 0, 1], angle_deg: 90}
subassemblies:
- {id: fixture, title: Joint geometry fixture, parts: [], occurrences: ['@pad']}
joints:
- {id: J-601, type: revolute, parent: TST-0602, child: TST-0601, axis: [0, 0, 1], origin: [0, 0, 0], min: -10, max: 10, nominal: 0}
- {id: J-602, type: revolute, parent: TST-0603, child: TST-0604, axis: [0, 0, 1], origin: [0, 0, 0], min: -10, max: 10, nominal: 0}
- {id: J-603, type: fixed, parent: TST-0605, child: '@pad', axis: [1, 0, 0], origin: [0, 0, 0], min: 0, max: 0, nominal: 0}
- {id: J-604, type: fixed, parent: TST-0605, child: TST-0607, axis: [1, 0, 0], origin: [0, 0, 0], min: 0, max: 0, nominal: 0}
- {id: J-605, type: revolute, parent: TST-0605, child: TST-0608, axis: [0, 0, 1], origin: [0, 0, 0], min: -10, max: 10, nominal: 0}
- {id: J-606, type: revolute, parent: TST-0602, child: TST-0609, axis: [0, 0, 1], origin: [0, 0, 0], min: -10, max: 10, nominal: 0}
- {id: J-607, type: fixed, parent: TST-0611, child: TST-0610, axis: [0, 0, 1], origin: [0, 0, 0], min: 0, max: 0, nominal: 0}
- {id: J-616, type: revolute, parent: TST-0616, child: TST-0615, axis: [0, 0, 1], origin: [7, 7, 0], min: -10, max: 10, nominal: 0}
- {id: J-617, type: revolute, parent: TST-0617, child: TST-0601, axis: [0, 0, 1], origin: [0, 0, 0], min: -10, max: 10, nominal: 0}
- {id: J-618, type: revolute, parent: TST-0602, child: TST-0618, axis: [0, 0, 1], origin: [0, 0, 0], min: -10, max: 10, nominal: 0}
- {id: J-612, type: fixed, parent: TST-0605, child: TST-0608, axis: [1, 0, 0], origin: [1000, 1000, 1000], min: 0, max: 0, nominal: 0}
- {id: J-613, type: fixed, parent: TST-0605, child: TST-0608, axis: [1, 0, 0], origin: [0, 0, -50], min: 0, max: 0, nominal: 0}
- {id: J-614, type: fixed, parent: TST-0605, child: TST-0608, axis: [0, 0, 1], origin: [0, 0, 20], min: 0, max: 0, nominal: 0}
- {id: J-615, type: fixed, parent: TST-0605, child: TST-0608, axis: [0, 0, 1], origin: [3, 4, 5], min: 0, max: 0, nominal: 0}
- {id: J-619, type: fixed, parent: TST-0605, child: TST-0614, axis: [1, 0, 0], origin: [0, 0, 0], min: 0, max: 0, nominal: 0}
- {id: J-620, type: fixed, parent: TST-0612, child: TST-0613, axis: [1, 0, 0], origin: [0, 0, 0], min: 0, max: 0, nominal: 0}
- {id: J-622, type: fixed, parent: TST-0605, child: TST-0608, axis: [0, 0, 1], origin: [1000, 0, 5], min: 0, max: 0, nominal: 0}
- {id: J-623, type: fixed, parent: TST-0605, child: TST-0608, axis: [1, 0, 0], origin: [0, 1000, -50], min: 0, max: 0, nominal: 0}
- {id: J-624, type: fixed, parent: TST-0620, child: TST-0608, axis: [1, 0, 0], origin: [0, 0, -50], min: 0, max: 0, nominal: 0}
- {id: J-625, type: fixed, parent: TST-0605, child: TST-0621, axis: [1, 0, 0], origin: [0, 0, -50], min: 0, max: 0, nominal: 0}
- {id: J-626, type: fixed, parent: TST-0605, child: TST-0608, axis: [0, 0, 1], origin: [1000, 0, 5], min: 0, max: 0, nominal: 0,
   mount: {point: [2, 1, 5]}}
- {id: J-627, type: fixed, parent: TST-0605, child: TST-0608, axis: [0, 0, 1], origin: [1000, 0, 5], min: 0, max: 0, nominal: 0,
   mount: {point: [30, 0, 5]}}
"""


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the CAD shape fixtures")
class JointGeometryTests(unittest.TestCase):
    """The joint check reads each endpoint's mating geometry, not its sourcing: a bore or a shank on the joint axis,
    or, for a fixed joint, planar face contact between the two parts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        hw, parts_dir = build_parts(Path(cls.tmp.name), JOINT_GEOMETRY_PARTS)
        (hw / "assembly.yaml").write_text(JOINT_GEOMETRY_ASSEMBLY, encoding="utf-8")
        cls.code, cls.report = form_check(parts_dir, "F1", hw)

    def found(self, joint: str) -> dict:
        return self.report["joints"][joint]["parts"]

    def missing(self, joint: str) -> dict[str, dict]:
        return {f["part"]: f for f in self.report["findings"] if f["check"] == "joint_bore_missing" and f["joint"] == joint}

    def test_a_made_shaft_in_a_bought_bore_passes(self) -> None:
        found = self.found("J-601")
        self.assertEqual(self.missing("J-601"), {})
        self.assertEqual((found["TST-0601"]["kind"], found["TST-0602"]["kind"]), ("shank", "bore"))
        self.assertEqual((found["TST-0601"]["offset_mm"], found["TST-0602"]["offset_mm"]), (0.0, 0.0))

    def test_a_made_bore_with_a_bought_shank_passes(self) -> None:
        found = self.found("J-602")
        self.assertEqual(self.missing("J-602"), {})
        self.assertEqual((found["TST-0603"]["kind"], found["TST-0604"]["kind"]), ("bore", "shank"))

    def test_a_flat_mounted_fixed_joint_passes_by_face_contact(self) -> None:
        self.assertEqual(self.missing("J-603"), {})
        for ref in ("TST-0605", "@pad"):
            with self.subTest(part=ref):
                contact = self.found("J-603")[ref]
                self.assertEqual(contact["kind"], "face")
                self.assertLessEqual(contact["offset_mm"], 1e-6)
                self.assertAlmostEqual(contact["area_mm2"], 200.0, places=3)

    def test_a_fixed_joint_with_a_gap_and_no_cylinder_fails(self) -> None:
        missing = self.missing("J-604")
        self.assertEqual(sorted(missing), ["TST-0605", "TST-0607"])
        finding = missing["TST-0607"]
        self.assertEqual((finding["kind"], finding["nearest"], finding["blocking"]), ("bore, shank or face contact", None, True))
        self.assertAlmostEqual(finding["contact"]["offset_mm"], 0.5, places=6)
        self.assertAlmostEqual(finding["contact"]["area_mm2"], 200.0, places=3)

    def test_a_revolute_joint_without_a_cylinder_fails_despite_face_contact(self) -> None:
        missing = self.missing("J-605")
        self.assertEqual(sorted(missing), ["TST-0605", "TST-0608"])
        self.assertEqual({f["kind"] for f in missing.values()}, {"bore or shank"})
        self.assertEqual({"contact" in f for f in missing.values()}, {False})

    def test_a_misaligned_cylinder_fails(self) -> None:
        missing = self.missing("J-606")
        self.assertEqual(sorted(missing), ["TST-0609"])
        nearest = missing["TST-0609"]["nearest"]
        self.assertEqual(nearest["kind"], "shank")
        self.assertAlmostEqual(nearest["offset_mm"], 0.2, places=6)
        self.assertEqual(self.code, 1)

    def test_a_roller_on_its_axle_names_the_bore_not_its_outside(self) -> None:
        found = self.found("J-607")
        self.assertEqual(self.missing("J-607"), {})
        self.assertEqual((found["TST-0611"]["kind"], found["TST-0611"]["radius"]), ("shank", 3.0))
        self.assertEqual((found["TST-0610"]["kind"], found["TST-0610"]["radius"]), ("bore", 3.0))

    def test_an_edge_fillet_on_the_joint_axis_is_no_shank(self) -> None:
        missing = self.missing("J-616")
        self.assertEqual(sorted(missing), ["TST-0615"])
        self.assertIsNone(missing["TST-0615"]["nearest"])

    def test_a_half_bore_journal_and_a_d_shaft_still_mate(self) -> None:
        for joint, part, kind in (("J-617", "TST-0617", "bore"), ("J-618", "TST-0618", "shank")):
            with self.subTest(joint=joint):
                self.assertEqual(self.missing(joint), {})
                found = self.found(joint)[part]
                self.assertEqual((found["kind"], found["radius"], found["offset_mm"]), (kind, 5.0, 0.0))

    def test_face_contact_far_from_the_joint_fails(self) -> None:
        """The pad sits flush on the deck, but the joint's datum plane (x = 1000) is 990 mm from the contact."""
        missing = self.missing("J-612")
        self.assertEqual(sorted(missing), ["TST-0605", "TST-0608"])
        contact = missing["TST-0608"]["contact"]
        self.assertEqual((contact["kind"], contact["offset_mm"]), ("face", 0.0))
        self.assertAlmostEqual(contact["datum_mm"], 990.0, places=6)

    def test_face_contact_at_the_joint_datum_passes(self) -> None:
        """An offset datum (x = 0, framed 55 mm below the deck, as a V6 head on the crank axis) cuts the contact; a
        datum normal to the contact (z = 5) lies in it."""
        for joint in ("J-613", "J-615"):
            with self.subTest(joint=joint):
                self.assertEqual(self.missing(joint), {})
                self.assertEqual(self.found(joint)["TST-0608"]["datum_mm"], 0.0)

    def test_a_datum_parallel_to_the_contact_but_off_it_fails(self) -> None:
        missing = self.missing("J-614")
        self.assertEqual(sorted(missing), ["TST-0605", "TST-0608"])
        self.assertAlmostEqual(missing["TST-0608"]["contact"]["datum_mm"], 15.0, places=6)

    def test_a_slightly_tilted_pad_within_tolerance_passes(self) -> None:
        """Its largest separation, at the pad's edge, is 0.02 + 10 sin(0.003 deg) mm."""
        self.assertEqual(self.missing("J-619"), {})
        contact = self.found("J-619")["TST-0614"]
        self.assertEqual(contact["kind"], "face")
        self.assertAlmostEqual(contact["offset_mm"], 0.02 + 10 * math.sin(math.radians(0.003)), places=5)
        self.assertAlmostEqual(contact["area_mm2"], 400.0, delta=0.01)

    def test_separation_is_checked_across_the_whole_patch(self) -> None:
        """0.045 mm at the centre is within tolerance; 0.045 + 100 sin(0.005 deg) mm at the far end is not."""
        missing = self.missing("J-620")
        self.assertEqual(sorted(missing), ["TST-0612", "TST-0613"])
        self.assertAlmostEqual(missing["TST-0613"]["contact"]["offset_mm"], 0.045 + 100 * math.sin(math.radians(0.005)),
                               places=5)

    def unknown(self, joint: str) -> dict[str, dict]:
        return {f["part"]: f for f in self.report["findings"] if f["check"] == "joint_contact_unknown" and f["joint"] == joint}

    def test_contact_on_a_face_normal_datum_but_a_metre_from_its_origin_is_unknown(self) -> None:
        """The datum plane z = 5 is the contact plane, but the origin is 990 mm off the pad: never a pass."""
        self.assertEqual(self.missing("J-622"), {})
        unknown = self.unknown("J-622")
        self.assertEqual(sorted(unknown), ["TST-0605", "TST-0608"])
        finding = unknown["TST-0608"]
        self.assertEqual((finding["status"], finding["blocking"]), ("unknown", True))
        self.assertIn("mount.point", finding["remedy"])
        self.assertEqual(finding["contact"]["locator"], "origin")
        self.assertAlmostEqual(finding["contact"]["locator_mm"], 990.0, places=6)

    def test_contact_cut_by_an_offset_datum_a_metre_from_the_origin_is_unknown(self) -> None:
        """The datum plane x = 0 cuts the pad, but the origin projects onto the deck plane 995 mm off it."""
        self.assertEqual(self.missing("J-623"), {})
        finding = self.unknown("J-623")["TST-0608"]
        self.assertIn("mount.point", finding["remedy"])
        self.assertEqual(finding["contact"]["locator"], "projection")
        self.assertAlmostEqual(finding["contact"]["locator_mm"], 995.0, places=6)

    def test_islands_either_side_of_the_datum_fail(self) -> None:
        """One overlap Boolean gives two islands, each 5 mm from the datum plane x = 0: the plane runs between them."""
        missing = self.missing("J-624")
        self.assertEqual(sorted(missing), ["TST-0608", "TST-0620"])
        self.assertAlmostEqual(missing["TST-0608"]["contact"]["datum_mm"], 5.0, places=6)

    def test_an_offset_mount_over_an_opening_in_its_contact_passes(self) -> None:
        """The origin projects into the pad's Ø4 hole: inside the contact's outline, as a head over its bore."""
        self.assertEqual((self.missing("J-625"), self.unknown("J-625")), ({}, {}))
        contact = self.found("J-625")["TST-0621"]
        self.assertEqual((contact["kind"], contact["locator"], contact["locator_mm"], contact["datum_mm"]),
                         ("face", "projection", 0.0, 0.0))

    def test_a_declared_mount_point_locates_the_contact(self) -> None:
        self.assertEqual((self.missing("J-626"), self.unknown("J-626")), ({}, {}))
        self.assertEqual(self.found("J-626")["TST-0608"]["locator"], "mount")
        missing = self.missing("J-627")
        self.assertEqual(sorted(missing), ["TST-0605", "TST-0608"])
        self.assertAlmostEqual(missing["TST-0608"]["contact"]["locator_mm"], 20.0, places=6)


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the CAD shape fixtures")
class JointContactFailureTests(unittest.TestCase):
    """A face-contact Boolean the kernel cannot complete leaves the joint unknown, with its reason: it is no empty
    intersection, so the joint is not reported as missing its mating geometry."""

    def test_a_failed_contact_boolean_is_unknown_not_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hw, parts_dir = build_parts(Path(tmp), {pn: JOINT_GEOMETRY_PARTS[pn] for pn in ("TST-0605", "TST-0608")})
            (hw / "assembly.yaml").write_text(
                "joints:\n- {id: J-701, type: fixed, parent: TST-0605, child: TST-0608, axis: [1, 0, 0], "
                "origin: [0, 0, 0], min: 0, max: 0, nominal: 0}\n", encoding="utf-8")
            header = re.search(r"(?ms)^# /// script$.*?^# ///$", (SCRIPTS / "form_check.py").read_text(encoding="utf-8"))
            assert header is not None
            wrapper = Path(tmp) / "failing_boolean.py"
            wrapper.write_text(header.group(0) + "\n" + FAILING_BOOLEAN, encoding="utf-8")
            code, report = run_json(["uv", "run", "--script", str(wrapper), str(SCRIPTS / "form_check.py"), "--hw", str(hw),
                                     "--parts-dir", str(parts_dir), "--fidelity", "F1"], parse=result_payload)
        joint = [f for f in report["findings"] if f.get("joint") == "J-701"]
        self.assertEqual((code, sorted((f["check"], f["part"]) for f in joint)),
                         (1, [("joint_contact_unknown", "TST-0605"), ("joint_contact_unknown", "TST-0608")]))
        for finding in joint:
            with self.subTest(part=finding["part"]):
                self.assertEqual((finding["status"], finding["blocking"]), ("unknown", True))
                self.assertIn("Boolean", finding["reason"])
        self.assertEqual({row["kind"] for row in report["joints"]["J-701"]["parts"].values()}, {"unknown"})


# Runs form_check (argv[1]) with every face-to-face BOPAlgo Boolean reporting a kernel error, as cad-model's
# occ.boolean reads it.
FAILING_BOOLEAN = '''
import importlib.util
import sys

import OCP.BOPAlgo as bop
from OCP.TopAbs import TopAbs_FACE


class FailingBOP(bop.BOPAlgo_BOP):
    forced = False

    def SetArguments(self, shapes):
        self.forced = all(shape.ShapeType() == TopAbs_FACE for shape in shapes)
        super().SetArguments(shapes)

    def HasErrors(self):
        return self.forced or super().HasErrors()


bop.BOPAlgo_BOP = FailingBOP
spec = importlib.util.spec_from_file_location("form_check", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.exit(module.main(sys.argv[2:]))
'''


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the CAD shape fixtures")
class WallOverrideTests(unittest.TestCase):
    """product.yaml manufacturing.min_wall_mm gives a process a minimum, and an invalid one stops the check."""

    def test_cnc_override_sets_a_minimum_and_an_invalid_override_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hw, parts_dir = build_parts(Path(tmp), {"TST-0421": ("process: cnc\nmake_buy: make\nform_class: plate\n",
                                                                 PLATE_08)})
            product = hw / "product.yaml"
            product.write_text("id: fixture\nmanufacturing:\n  min_wall_mm: {cnc: 1.0}\n", encoding="utf-8")
            code, report = form_check(parts_dir, "F2", hw)
            self.assertEqual((code, [(f["check"], f["thickness_mm"], f["min_mm"], f["blocking"]) for f in report["findings"]]),
                             (0, [("min_wall", 0.8, 1.0, False)]))
            product.write_text("id: fixture\nmanufacturing:\n  min_wall_mm: {cnc: 0.5}\n", encoding="utf-8")
            code, report = form_check(parts_dir, "F2", hw)
            wall = report["parts"]["TST-0421"]["min_wall"]
            self.assertEqual((code, report["findings"], wall["required_mm"], wall["thickness_mm"]), (0, [], 0.5, 0.8))
            product.write_text("id: fixture\nmanufacturing:\n  min_wall_mm: {cnc: 0}\n", encoding="utf-8")
            code, report = form_check(parts_dir, "F2", hw)
            self.assertEqual((code, report["status"]), (2, "error"))
            self.assertIn("manufacturing.min_wall_mm.cnc", report["error"])


@unittest.skipUnless(importlib.util.find_spec("yaml"), "pyyaml is required to read product.yaml")
class WallThresholdTests(unittest.TestCase):
    """Only written min_wall_mm overrides apply (the schema's default is never one); anything else the product schema
    rejects is a RecordError."""

    def thresholds(self, product: str) -> dict[str, float]:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "product.yaml").write_text(product, encoding="utf-8")
            return FORM_CHECK.minimum_wall_overrides(Path(tmp))

    def test_only_written_overrides_apply(self) -> None:
        self.assertEqual(self.thresholds("id: fixture\n"), {})
        self.assertEqual(self.thresholds("id: fixture\nmanufacturing:\n  processes: [fdm]\n"), {})
        self.assertEqual(self.thresholds("id: fixture\nmanufacturing:\n  min_wall_mm: {fdm: 1, cnc: 0.8}\n"),
                         {"fdm": 1.0, "cnc": 0.8})

    def test_invalid_overrides_are_rejected(self) -> None:
        for value in ("{fdm: 0}", "{fdm: -1.2}", "{fdm: '1.2'}", "{fdm: true}", "{fdm: .nan}", "{cnc: .inf}",
                      "{cnc: null}", "{purchased: 1.0}", "{FDM: 1.2}", "{}", "1.2", "[1.2]", "null"):
            with self.subTest(min_wall_mm=value), self.assertRaisesRegex(FORM_CHECK.RecordError, "min_wall_mm"):
                self.thresholds(f"id: fixture\nmanufacturing:\n  min_wall_mm: {value}\n")

    def test_overridable_processes_are_the_product_schema_keys(self) -> None:
        schema = json.loads((SKILL.parent / "cad-record" / "schemas" / "product.schema.json").read_text(encoding="utf-8"))
        keys = schema["properties"]["manufacturing"]["properties"]["min_wall_mm"]["properties"]
        self.assertEqual(sorted(FORM_CHECK.MIN_WALL_PROCESSES), sorted(keys))


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the cad-motion block fixtures")
class BlockDetectorTests(unittest.TestCase):
    """The cad-motion stand-in lamp is boxes and cylinders: every made part must be flagged."""

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            built = subprocess.run(["uv", "run", "--script", str(FIXTURES), tmp], capture_output=True, text=True)
            if built.returncode != 0:
                raise AssertionError(f"build_fixtures.py exited {built.returncode}:\n{built.stderr[-2000:]}")
            cls.f2 = form_check(Path(tmp) / "lamp", "F2")
            cls.f1 = form_check(Path(tmp) / "lamp", "F1")

    def test_fixture_blocks_are_flagged_and_their_joints_still_pass(self) -> None:
        code, report = self.f2
        self.assertEqual(code, 0)  # block_like is a diagnostic: it never fails the check (decision 3)
        flagged = sorted(f["part"] for f in report["findings"] if f["check"] == "block_like" and not f["blocking"])
        self.assertEqual(flagged, MADE_PARTS)
        self.assertEqual([f for f in report["findings"] if f["check"] != "block_like"], [])

    def test_f1_does_not_judge_form(self) -> None:
        self.assertEqual((self.f1[0], self.f1[1]["findings"]), (0, []))


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the kernel environments")
class KernelDefectTests(unittest.TestCase):
    """Defects only the in-memory shape shows: the STEP read-back alone passes both parts."""

    def test_kernel_invalid_face_and_loose_fillets_are_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            code, report = run_json([sys.executable, str(SCRIPTS / "build_reference.py"), "--kernel", "cadquery",
                                     "--models", str(DEFECTS / "cadquery"), "--out", str(out)])
            self.assertEqual((code, report["errors"]), (0, []))
            code, report = form_check(out, "F0")
        self.assertEqual(code, 1)
        self.assertTrue(all(row["valid"] for row in report["parts"].values()), report["parts"])
        found = sorted((f["check"], f["part"], f.get("source"), f["blocking"]) for f in report["findings"])
        self.assertEqual(found, [("brep_invalid", "TST-0101", "kernel", True), ("step_readback", "TST-0101", None, True),
                                 ("step_readback", "TST-0102", None, True)])


PARITY_MODELS = {
    "cadquery": {
        "TST-0201": 'import cadquery as cq\nKERNEL = "cadquery"\nPARAMS = {"size": float("nan")}\n\n\n'
                    'def build(params):\n    return cq.Workplane().box(10, 10, 10)\n',
        "TST-0202": 'import cadquery as cq\nKERNEL = "cadquery"\nPARAMS = {"size": 10.0}\n\n\n'
                    'def build(params):\n    s = params["size"]\n'
                    '    return cq.Workplane().pushPoints([(0, 0), (2 * s, 0)]).box(s, s, s, combine=False)\n',
    },
    "build123d": {
        "TST-0203": 'import bd_warehouse  # noqa: F401 - in the build123d runner environment\n'
                    'from build123d import Box\nKERNEL = "build123d"\nPARAMS = {"size": 10.0}\n\n\n'
                    'def build(params):\n    return Box(params["size"], params["size"], params["size"])\n',
    },
}


@unittest.skipUnless(shutil.which("uv"), "uv is required to build the kernel environments")
class RunnerParityTests(unittest.TestCase):
    """build_reference builds through cad-model's runners: same environment, same model.py contract."""

    def build(self, kernel: str, tmp: str) -> tuple[int, dict]:
        models = Path(tmp) / "models"
        models.mkdir()
        for pn, source in PARITY_MODELS[kernel].items():
            (models / f"{pn}.py").write_text(source, encoding="utf-8")
        return run_json([sys.executable, str(SCRIPTS / "build_reference.py"), "--kernel", kernel,
                         "--models", str(models), "--out", str(Path(tmp) / "out")])

    def test_cadquery_rejects_nan_params_and_keeps_every_workplane_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.build("cadquery", tmp)
        self.assertEqual(code, 1)
        self.assertEqual([e.split(":")[:2] for e in report["errors"]], [["TST-0201", " ContractError"]])
        self.assertAlmostEqual(report["parts"]["TST-0202"]["kernel_volume_mm3"], 2000.0, places=6)

    def test_build123d_models_can_use_bd_warehouse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.build("build123d", tmp)
        self.assertEqual((code, report["errors"], sorted(report["parts"])), (0, [], ["TST-0203"]))


if __name__ == "__main__":
    unittest.main()
