#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Feature recognition on real B-rep fixtures, and the gate findings that name an undimensioned feature.

The fixtures are built and recognised in freecadcmd (tests/fixtures/recognition_fixtures_job.py);
the cases are skipped only when no FreeCAD is installed. The gate cases run feature_coverage on that
recognition with a binding and measured characteristics made here.
Run: uv run --with pyyaml --with defusedxml --with jsonschema python -m unittest discover -s cad/skills/cad-drawings/tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import drawing_intent  # noqa: E402
import completeness  # noqa: E402
import feature_coverage  # noqa: E402
import feature_geometry as fg  # noqa: E402
import freecad_runtime  # noqa: E402
from drawing_intent import Characteristic, DatumFrame, Measured  # noqa: E402
from feature_geometry import Binding, Face, ResolvedFeature  # noqa: E402
from feature_recognition import Recognition, Recognized  # noqa: E402

FREECADCMD = freecad_runtime.find_freecadcmd()
FREECAD_PRESENT = FREECADCMD is not None and FREECADCMD.is_file()
JOB = HERE / "fixtures" / "recognition_fixtures_job.py"
FRAME = DatumFrame((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), False, {})
# outward normals of the bevelled blocks' planes: the x = 0 end, the y = 0 side, the bottom and the top
LEFT, SIDE, BOTTOM, TOP = (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0), (0.0, 0.0, 1.0)


def sizes(recognition: Recognition, category: str) -> list[dict[str, float]]:
    return [{s.name: round(s.value, 3) for s in item.sizes} | {"instances": item.details.get("instances", 1)}
            for item in recognition.features if item.category == category]


def binding(recognition: Recognition, faces: dict[int, Face], kind_of) -> Binding:
    """Every face bound: one feature per recognised feature, named after it, of the kind ``kind_of`` gives."""
    owner, features = {}, {}
    for item in recognition.features:
        free = [i for i in item.faces if i not in owner]
        if free:
            features[item.id] = ResolvedFeature(item.id, kind_of(item), tuple(free))
            owner.update({i: item.id for i in free})
    return Binding(features, owner, ())


@unittest.skipUnless(FREECAD_PRESENT, "recognition fixtures need FreeCAD's freecadcmd")
class Recognition_(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-recognition-") as work:
            data, message = freecad_runtime.run_job(FREECADCMD, JOB, {"scripts": str(SCRIPTS)}, Path(work), 600)
        if data is None:
            raise AssertionError(message)
        cls.fixtures = {name: (Recognition.from_dict(f["recognized"]), {r["index"]: Face.from_dict(r) for r in f["faces"]})
                        for name, f in data["fixtures"].items()}

    def test_plate_pocket_and_holes(self) -> None:
        recognition, _ = self.fixtures["plate"]
        self.assertNotIn("wall", {item.category for item in recognition.features if 6.0 in
                                  [round(s.value, 3) for s in item.sizes]})   # a 6 mm floor under a 20 mm pocket is not thin
        pocket = sizes(recognition, "pocket")
        self.assertEqual(len(pocket), 1, recognition.features)
        self.assertEqual((pocket[0]["depth"]), 4.0)
        widths = sorted(s.value for item in recognition.features if item.category == "pocket"
                        for s in item.sizes if s.name == "width")
        self.assertEqual([round(w, 3) for w in widths], [15.0, 20.0])
        holes = sorted(sizes(recognition, "hole"), key=lambda s: s["diameter"])
        self.assertEqual(holes, [{"diameter": 4.0, "depth": 6.0, "instances": 1}, {"diameter": 5.0, "instances": 2}])
        self.assertEqual(recognition.unrecognized, ())

    def test_shelled_box_walls_and_cutout(self) -> None:
        recognition, _ = self.fixtures["shell"]
        cavity = sizes(recognition, "pocket")
        self.assertEqual([(c["depth"]) for c in cavity], [18.0])
        cutout = [s for item in recognition.features if item.category == "cutout" for s in item.sizes]
        self.assertEqual(sorted(round(s.value, 3) for s in cutout if s.name == "width"), [6.0, 10.0])
        walls = [w["thickness"] for w in sizes(recognition, "wall")]
        self.assertGreaterEqual(len(walls), 4, recognition.features)     # four side walls and the floor
        self.assertEqual(set(walls), {2.0})

    def test_slotted_part(self) -> None:
        recognition, _ = self.fixtures["slotted"]
        self.assertEqual(sizes(recognition, "slot"), [{"width": 6.0, "radius": 3.0, "length": 20.0, "depth": 3.0,
                                                       "instances": 1}])

    def test_chamfered_boss_height_runs_to_the_plane_beyond_the_chamfer(self) -> None:
        recognition, faces = self.fixtures["chamfered_boss"]
        boss = next(item for item in recognition.features if item.category == "boss")
        height = next(s for s in boss.sizes if s.name == "height")
        self.assertAlmostEqual(height.value, 10.0, places=6)
        base, top = height.between
        self.assertEqual([round(faces[i].point[2], 6) for i in base], [5.0])
        self.assertEqual([round(faces[i].point[2], 6) for i in top], [15.0], "the end plane beyond the 0.5 chamfer")

    def test_overall_only_intent_fails_naming_each_feature(self) -> None:
        """Envelope planes located, everything else one controlled-model surface: the sheet passed before."""
        recognition, faces = self.fixtures["shell"]
        bound = binding(recognition, faces, lambda item: "plane_face" if item.category == "outline" else "surface")
        report = feature_coverage.check(recognition, bound, Measured(frame=FRAME), faces)
        named = {f["feature"] for f in report.findings}
        self.assertTrue({"pocket_1", "cutout_1"} <= named, report.findings)
        self.assertTrue(any(f["feature"].startswith("wall") for f in report.findings))
        cutout = next(f for f in report.findings if f["feature"] == "cutout_1")
        self.assertIn("controlled-model surface", cutout["message"])
        self.assertIn("width 10", cutout["message"])
        # one finding per feature, however many faces it has
        self.assertEqual(len(report.findings), len(named))

    def test_wall_thickness_needs_its_own_dimension(self) -> None:
        recognition, faces = self.fixtures["shell"]
        # the +X wall: both faces off the datum planes, so a location of either is no thickness
        wall = next(item for item in recognition.features if item.category == "wall"
                    and item.direction == (1.0, 0.0, 0.0) and min(faces[i].point[0] for i in item.faces) > 1.0)
        size = wall.sizes[0]
        a, b = size.between[0][0], size.between[1][0]
        bound = binding(recognition, faces, lambda item: "plane_face")
        located = Measured(frame=FRAME)
        for index in (a, b):   # both faces located from the datum frame: the thickness is only a difference
            located.characteristics.append(Characteristic(f"loc{index}", bound.owner[index], "location",
                                                          faces[index].point[0], (index,), axis="x"))
        report = feature_coverage.check(recognition, bound, located, faces)
        self.assertTrue(any(f["feature"] == wall.id and "thickness 2" in f["message"] for f in report.findings))
        located.characteristics.append(Characteristic("wall_t", bound.owner[a], "thickness", 2.0, (b,),
                                                      details={"features": [bound.owner[a], bound.owner[b]]}))
        report = feature_coverage.check(recognition, bound, located, faces)
        self.assertFalse(any(f["feature"] == wall.id for f in report.findings), report.findings)

    def test_drafted_walls_inclined_flanks_and_a_dish_are_sized_at_their_planes(self) -> None:
        recognition, faces = self.fixtures["drafted_dish"]
        drafted = [item for item in recognition.features if item.category == "wall" and item.details.get("drafted")]
        self.assertEqual(len(drafted), 2, recognition.features)
        for wall in drafted:   # 3 thick at the rim, not at the 3 deg drafted faces' mean
            thickness = next(s for s in wall.sizes if s.name == "thickness")
            self.assertAlmostEqual(thickness.value, 3.0, places=6)
            self.assertEqual([round(faces[i].point[2], 6) for i in thickness.at], [20.0])
            self.assertAlmostEqual(next(s.value for s in wall.sizes if s.name == "angle"), 3.0, places=4)
        self.assertEqual(sizes(recognition, "dish"), [{"radius": 40.4, "depth": 0.8, "instances": 1}])
        dovetail = next(item for item in recognition.features if item.category == "boss" and any(s.at for s in item.sizes))
        width = next(s for s in dovetail.sizes if s.at)
        self.assertAlmostEqual(width.value, 20.0, places=6)
        self.assertEqual([round(faces[i].point[2], 6) for i in width.at], [-8.0], "at the bed, between the flanks")

    def test_a_drafted_wall_flank_width_or_dish_needs_its_size_at_a_plane_not_a_profile(self) -> None:
        recognition, faces = self.fixtures["drafted_dish"]
        # every face its own feature: the frame's planes located, inclined planes and the sphere model-defined
        features = {f"f{i}": ResolvedFeature(f"f{i}", "plane_face" if face.support_type == "plane" and any(
            abs(abs(c) - 1.0) < 1e-9 for c in face.normal) else "surface", (i,)) for i, face in faces.items()}
        bound = Binding(features, {i: f"f{i}" for i in faces}, ())
        targets = [item for item in recognition.features if item.category == "dish" or any(s.at for s in item.sizes)]
        self.assertEqual(sorted(item.category for item in targets), ["boss", "dish", "wall", "wall"])
        report = feature_coverage.check(recognition, bound, Measured(frame=FRAME), faces)
        missing = {f["feature"]: f["message"] for f in report.findings}
        for item in targets:
            self.assertIn(item.id, missing)
        dish = next(t.id for t in targets if t.category == "dish")
        self.assertIn("SR 40.4", missing[dish])
        self.assertIn("depth 0.8", missing[dish])
        for item in targets:
            if item.category != "dish":
                self.assertIn("at the plane of face", missing[item.id])
            if item.category == "wall":   # the drafted face is a controlled-model surface: its profile is not the draft
                self.assertIn("angle 3", missing[item.id])
        dimensions = []
        for item in targets:
            plane = next((s.at[0] for s in item.sizes if s.at), None)
            for size in item.sizes:
                name = f"{item.id}_{size.name}"
                if size.at:
                    dimensions.append({"id": name, "operation": "thickness" if size.name == "thickness" else "width",
                                       "features": sorted({f"f{size.between[0][0]}", f"f{size.between[1][0]}"}),
                                       "at": f"f{plane}"})
                elif size.name == "angle":   # from the reference plane: 87° states a 3° draft, 70° a flank
                    dimensions.append({"id": name, "operation": "angle", "features": [f"f{size.faces[0]}", f"f{plane}"]})
                elif item.category == "dish":   # SR, and the depth from the plane it opens through
                    entry = [f"f{size.between[0][0]}"] if size.name == "depth" else []
                    dimensions.append({"id": name, "operation": size.name, "features": [f"f{item.faces[0]}", *entry]})
        measurer = drawing_intent.Measurer("TST-0002", {"drawing": {"type": "detail", "dimensions": dimensions}}, {}, [],
                                           bound, list(faces.values()))
        measurer._requests(FRAME)
        self.assertEqual(measurer.result.findings, [])
        self.assertTrue(next(c for c in measurer.result.characteristics if c.operation == "radius").details["spherical"])
        report = feature_coverage.check(recognition, bound, Measured(FRAME, measurer.result.characteristics), faces)
        left = [m for f in report.findings if f["feature"] in {t.id for t in targets} for m in f["missing"]]
        # the dovetail's own length, height and position are other requests; nothing at a plane, no angle, no SR
        self.assertEqual([m for m in left if "at the plane" in m or m.startswith(("angle", "SR", "depth"))], [], left)
        self.assertNotIn(next(t.id for t in targets if t.category == "dish"), {f["feature"] for f in report.findings})

    def test_a_partly_stale_feature_is_named_by_the_gate_and_never_crashes_it(self) -> None:
        """One of two patches of a feature matches a face, the other no longer does: the feature leaves the binding
        with its face owners, and the coverage and completeness gates still run and name it."""
        recognition, faces = self.fixtures["plate"]
        locators, bound = {}, {}
        for number, item in enumerate(recognition.features):
            patches = [{"support_type": faces[i].support_type, "point_mm": list(faces[i].point or (0, 0, 0))}
                       for i in item.faces]
            matches = [[i] for i in item.faces]
            if number == 0:   # a bracket revision moved the second patch off every face
                patches.append({"support_type": "plane", "point_mm": [500.0, 500.0, 500.0]})
                matches.append([])
            locators[item.id] = {"kind": "surface", "patches": patches}
            bound[item.id] = matches
        stale = recognition.features[0].id
        binding = fg.resolve(locators, bound, list(faces.values())).usable()
        report = completeness.evaluate("TST-0009", {"drawing": {"type": "detail"}}, [], binding, Measured(frame=FRAME),
                                       None, "0" * 64, recognition=recognition, faces=faces)
        named = [f for f in report.findings if f.get("feature") == stale and f["code"] == "feature_not_found"]
        self.assertTrue(named, report.findings)

    def vcut_findings(self, opening: str):
        """Run the drawing intent on the V-cut plate with the engraving's `on` relation set to ``opening`` (top or under)."""
        _, faces = self.fixtures["vcut"]
        planes = {i: f for i, f in faces.items() if f.support_type == "plane"}
        top = next(i for i, f in planes.items() if f.normal == (0.0, 0.0, 1.0) and abs(f.point[2] - 10.0) < 1e-6)
        under = next(i for i, f in planes.items() if f.normal == (0.0, 0.0, -1.0))
        groove = tuple(sorted(i for i, f in planes.items() if 9.0 < f.point[2] < 9.9))
        self.assertEqual(len(groove), 4, "two flanks and two end triangles")
        features = {"top": ResolvedFeature("top", "plane_face", (top,)),
                    "under": ResolvedFeature("under", "plane_face", (under,)),
                    "mark": ResolvedFeature("mark", "engraving", groove, relations={"on": (opening,)})}
        part = {"process": "fdm", "drawing": {
            "type": "detail", "coordinate_frame": {"origin": {"at": "top"}, "z": {"normal_to": "top"}},
            "notes": [{"reference": "mark"}], "features": {"top": {}, "under": {}, "mark": {}}}}
        measured = drawing_intent.measure("TST-0008", part, {}, [], Binding(features, {}, ()), list(faces.values()))
        return measured, groove

    def test_a_v_cut_is_as_deep_as_its_lowest_point_below_the_plane_it_opens_through(self) -> None:
        measured, groove = self.vcut_findings("top")
        self.assertEqual(measured.findings, [])
        depth = next(c for c in measured.characteristics if c.feature == "mark" and c.operation == "depth")
        self.assertAlmostEqual(depth.value, 1.0, places=6)
        self.assertEqual(sorted(depth.faces), list(groove))

    def test_a_v_cut_opening_named_as_the_underside_is_refused_naming_the_engraving_and_the_plane(self) -> None:
        measured, _ = self.vcut_findings("under")   # the 10 mm away face; the cut does not open through it
        self.assertEqual([c for c in measured.characteristics if c.operation == "depth"], [])
        (finding,) = [f for f in measured.findings if f.get("feature") == "mark"]
        self.assertEqual(finding["code"], "measurement_failed")
        self.assertIn("mark", finding["message"])
        self.assertIn("under", finding["message"])

    def stated_bevel(self, fixture: str, longer: tuple[float, float, float], shorter: tuple[float, float, float],
                     ) -> tuple[Recognized, Characteristic, list[dict], bool]:
        """Recognition's plane chamfer, the drawing's chamfer characteristic and the coverage findings of a
        bevelled block drawn with every face its own feature; and whether the face whose shared edge with the
        chamfer is longer (normal ``longer``) has the higher face index than the other (normal ``shorter``)."""
        recognition, faces = self.fixtures[fixture]
        (chamfer,) = [item for item in recognition.features if item.category == "chamfer"]

        def plane(normal: tuple[float, float, float]) -> int:
            return next(i for i, f in faces.items() if f.support_type == "plane"
                        and all(abs(a - b) < 1e-6 for a, b in zip(f.normal, normal)))

        kinds = {i: "chamfer" if i in chamfer.faces else "plane_face" if face.support_type == "plane" and any(
            abs(abs(c) - 1.0) < 1e-9 for c in face.normal) else "surface" for i, face in faces.items()}
        features = {f"f{i}": ResolvedFeature(f"f{i}", kind, (i,)) for i, kind in kinds.items()}
        bound = Binding(features, {i: f"f{i}" for i in faces}, ())
        part = {"drawing": {"type": "detail", "features": {name: {"size": "general"} for name in features},
                            "coordinate_frame": {"origin": {"intersection": [f"f{plane(n)}" for n in (LEFT, SIDE, BOTTOM)]},
                                                 "z": {"normal_to": f"f{plane(BOTTOM)}"},
                                                 "x": {"normal_to": f"f{plane(LEFT)}"}}}}
        measured = drawing_intent.measure("TST-0003", part, {}, [], bound, list(faces.values()))
        (char,) = [c for c in measured.characteristics if c.operation == "chamfer"]
        self.assertEqual([f for f in measured.findings if f.get("feature") == char.feature], [])
        report = feature_coverage.check(recognition, bound, measured, faces)
        return chamfer, char, [f for f in report.findings if f["feature"] == chamfer.id], plane(longer) > plane(shorter)

    def test_an_unequal_plane_chamfer_is_drawn_as_recognised_when_face_order_and_edge_order_disagree(self) -> None:
        """4.5 down the side, 4.7 across the top; the top's shared edge is the longer and has the higher index."""
        chamfer, char, missing, disagree = self.stated_bevel("bevel_top_longer", longer=TOP, shorter=SIDE)
        self.assertTrue(disagree, "premise: the face index order is not the edge length order")
        self.assertAlmostEqual(chamfer.sizes[0].value, 4.5, places=6)   # the leg along the top's normal
        self.assertAlmostEqual(chamfer.details["angle_deg"], 46.2, places=6)   # atan(4.7 / 4.5), to 0.1°
        self.assertAlmostEqual(char.value, 4.5, places=6)
        self.assertAlmostEqual(char.details["angle_deg"], 46.245, places=3)
        self.assertEqual(missing, [])

    def test_an_unequal_plane_chamfer_is_drawn_as_recognised_when_face_order_and_edge_order_agree(self) -> None:
        """The mirrored block: the side's shared edge is the longer and has the lower index."""
        chamfer, char, missing, disagree = self.stated_bevel("bevel_side_longer", longer=SIDE, shorter=TOP)
        self.assertFalse(disagree, "premise: the face index order is the edge length order")
        self.assertAlmostEqual(chamfer.sizes[0].value, 4.7, places=6)   # the leg along the side's normal
        self.assertAlmostEqual(chamfer.details["angle_deg"], 43.8, places=6)   # atan(4.5 / 4.7), to 0.1°
        self.assertAlmostEqual(char.value, 4.7, places=6)
        self.assertAlmostEqual(char.details["angle_deg"], 43.755, places=3)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
