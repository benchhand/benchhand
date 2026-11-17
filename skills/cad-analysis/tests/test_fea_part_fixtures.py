#!/usr/bin/env python3
"""Realistic part fixtures for the static FEA: seven parts, each with a material profile, supports, a service load
and a direction vector, built with the kernel cad-model uses and solved by fea_static.

Every fixture is a versioned case in one record version 2 project. The solve needs FreeCAD 1.1.4 and `uv`
(it builds the STEP through cadquery); without them the specification tests still run. Benchmark and test data
only: the sizes, loads and material numbers are typical of the part type, with synthetic datasheet sources.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import yaml

SKILLS = Path(__file__).resolve().parents[2]
SCRIPTS = SKILLS / "cad-analysis" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILLS / "cad-drawings" / "scripts"))

import fea_static  # noqa: E402
import freecad_runtime  # noqa: E402

_spec = importlib.util.spec_from_file_location("cad_record_mi_fixtures",
                                               SKILLS / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py")
assert _spec and _spec.loader
fx = sys.modules.get(_spec.name)
if fx is None:
    fx = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = fx
    _spec.loader.exec_module(fx)

FREECADCMD = freecad_runtime.find_freecadcmd()
FREECAD_PRESENT = FREECADCMD is not None and FREECADCMD.is_file() and os.access(FREECADCMD, os.X_OK)
UV = shutil.which("uv")
TOOLS = {"freecad": {"path": "/opt/freecad/freecadcmd", "version": "1.1.4"}}
CONDITIONS = {"conditioning": "dry", "temperature": {"value": 23, "units": "deg_C"}}

DATASHEET = fx.source("datasheet", "TST datasheet typical values")


def prop(name: str, value: float, units: str, **extra: Any) -> dict[str, Any]:
    data = fx.material_property(name, value, units, basis="supplier_typical", **extra)
    data["value"]["sources"] = [DATASHEET]
    return data


def profile(ident: str, form: str, process: str | None, *, E: float, nu: float, density: float, yield_mpa: float) -> dict[str, Any]:
    tested = fx.quantity(lower=10, upper=40, units="deg_C")
    data = fx.material_profile(ident, form=form, properties=[
        prop("youngs_modulus", E, "MPa", temperature=tested), prop("poisson_ratio", nu, "dimensionless", temperature=tested),
        prop("density", density, "kg_m3", temperature=tested), prop("yield_strength", yield_mpa, "MPa", temperature=tested)])
    if process is not None:
        data["process_state"] = {"process": process, "conditioning": "dry"}
    return data


# Typical room-temperature values of the grade; the sources are synthetic fixtures, not citations.
PROFILES = {
    "steel_s355": profile("steel_s355", "plate", None, E=210000, nu=0.30, density=7850, yield_mpa=355),
    "al_6061_t6": profile("al_6061_t6", "sheet", None, E=68900, nu=0.33, density=2700, yield_mpa=276),
    "abs_moulded": profile("abs_moulded", "moulded", "moulding", E=2300, nu=0.35, density=1050, yield_mpa=40),
    "pa6_moulded": profile("pa6_moulded", "moulded", "moulding", E=3000, nu=0.39, density=1140, yield_mpa=70),
}

Faces = list[dict[str, Any]]
Selector = dict[str, Any]


def plane(axis: str, value: float) -> Callable[[Faces], Selector]:
    return lambda faces: {"plane": {"axis": axis, "value": value, "tolerance": 1e-6}}


def cylinders(radius: float, **where: Any) -> Callable[[Faces], Selector]:
    """The cylindrical faces of this radius (to 0.01 mm), optionally limited to a centre-of-mass box."""
    def pick(faces: Faces) -> Selector:
        chosen = [f["index"] for f in faces if f["kind"] == "Cylinder" and abs(f["radius"] - radius) < 0.01
                  and all(lo <= f["centre"]["xyz".index(axis)] <= hi for axis, (lo, hi) in where.items())]
        if not chosen:
            raise LookupError(f"no cylinder of radius {radius} {where}")
        return {"indices": chosen}
    return pick


def flank(radius_range: tuple[float, float], angle_range: tuple[float, float]) -> Callable[[Faces], Selector]:
    """A planar face whose centre lies in a radius and angle window around the Z axis (a gear tooth flank)."""
    def pick(faces: Faces) -> Selector:
        chosen = []
        for f in faces:
            x, y, _ = f["centre"]
            radius, angle = math.hypot(x, y), math.atan2(y, x)
            if f["kind"] == "Plane" and radius_range[0] <= radius <= radius_range[1] and angle_range[0] <= angle <= angle_range[1]:
                chosen.append(f["index"])
        if len(chosen) != 1:
            raise LookupError(f"expected one flank in {radius_range} {angle_range}, found {chosen}")
        return {"indices": chosen}
    return pick


@dataclass(frozen=True)
class Fixture:
    name: str                                  # the fea_parts.py part name
    pn: str
    title: str
    material: str
    route: str                                 # the part's route kind
    fixed: Callable[[Faces], Selector]
    load: Callable[[Faces], Selector]
    force_n: float                             # service load magnitude
    direction: tuple[float, float, float]      # its unit vector, in the part's frame
    mesh_size: float
    aspect: float                              # bbox longest / shortest side
    note: str = ""
    load_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "load_id", f"{self.name}_load")


FIXTURE_SET = [
    Fixture("l_bracket", "TST-0401", "Sheet-metal L-bracket", "al_6061_t6", "sheet_metal", plane("z", 0), plane("x", 2),
            100.0, (1, 0, 0), 1.5, 1.5, "bolted down on its base, pulled sideways on the flange"),
    Fixture("moulded_housing", "TST-0402", "Ribbed moulded housing", "abs_moulded", "moulding", plane("z", 28), plane("z", 2.5),
            200.0, (0, 0, -1), 3.0, 80 / 30, "screwed down on the four boss tops, 0.05 MPa inside pressure on the floor"),
    Fixture("pillow_block", "TST-0403", "Machined pillow block", "steel_s355", "cnc", plane("z", 0), cylinders(12.5),
            25000.0, (0, 0, 1), 5.0, 100 / 40, "bolted down, 25 kN uplift on the bearing bore"),
    Fixture("pipe_flange", "TST-0404", "Pipe flange with bolt circle", "steel_s355", "cnc", cylinders(9.0), plane("z", 40),
            28000.0, (0, 0, 1), 8.0, 150 / 40, "held at its eight bolt holes, 10 MPa end load on the neck"),
    Fixture("keyed_shaft", "TST-0405", "Shaft with keyway and shoulders", "steel_s355", "cnc", plane("x", 140), plane("y", 4),
            3000.0, (0, 1, 0), 5.0, 140 / 40, "clamped at the 35 mm end, key force on the keyway flank"),
    Fixture("sprocket", "TST-0406", "Sixteen-tooth sprocket", "pa6_moulded", "moulding", cylinders(10.0),
            flank((35.0, 40.0), (-0.21 - 0.12, -0.21 + 0.12)), 300.0, (0, 1, 0), 2.0, 80 / 8,
            "held at the bore, 300 N on one tooth flank"),
    Fixture("truss_member", "TST-0407", "Warren-truss frame member", "al_6061_t6", "cnc", plane("x", 0), plane("x", 320),
            1500.0, (0, 0, -1), 6.0, 320 / 16, "clamped at one end, loaded at the other"),
]
BY_NAME = {f.name: f for f in FIXTURE_SET}


def documents() -> dict[str, Any]:
    """The record version 2 documents of the project: seven parts, four sourced profiles, one load each."""
    loads = [{"id": f.load_id, "kind": "static", "magnitude": fx.known(fx.quantity(f.force_n, "N")),
              "direction": list(f.direction), "requirements": ["REQ-001"]} for f in FIXTURE_SET]
    use = fx.use_case("draft", service_class=fx.known("working_prototype"), loads=loads)
    use["environment"]["temperature"] = fx.known(fx.quantity(lower=15, upper=30, units="deg_C"))
    requirements = fx.requirements([fx.requirement("REQ-001", "Carry the service load",
                                                   acceptance=[fx.criterion("yield_safety_factor", ">=", 1.0, "dimensionless")])])
    parts = [fx.part(f.pn, f.title, process="cnc" if f.route == "cnc" else f.route, material=f.material, part_intent=fx.intent(
        functions=[{"function": "structural", "requirements": ["REQ-001"]}], material_profile=f.material,
        service={"requirements": ["REQ-001"], "loads": [f.load_id]},
        critical_surfaces=fx.not_applicable("no critical surface in this fixture"),
        route=[fx.operation("make", f.route, fx.unknown("no capability in this fixture"))])) for f in FIXTURE_SET]
    return fx.record(product_data=fx.product(use, stage="analysis"), requirements_data=requirements, parts=parts,
                     materials=[copy.deepcopy(p) for p in PROFILES.values()], decisions=("DR-001",))


def lay_out(hw: Path, version: str = "v1") -> None:
    """Write the record below `hw` and snapshot it into versions/<version>, as the FEA tests lay out a version."""
    fx.write_record(hw, documents())
    folder = hw / "versions" / version
    record = folder / "record"
    for rel in documents():
        if not rel.startswith("_") and (hw / rel).is_file():
            (record / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(hw / rel, record / rel)
    files = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}
    (folder / "manifest.json").write_text(json.dumps({"label": version, "files": files}), encoding="utf-8")


def case_yaml(fixture: Fixture, faces: Faces) -> dict[str, Any]:
    """The case: the profile's conditions, the supports, and the service load as a force vector on its faces."""
    force = [round(fixture.force_n * c, 9) for c in fixture.direction]
    return {"step": f"{fixture.name}.step", "part": fixture.pn, "material": {"conditions": dict(CONDITIONS)},
            "acceptance": {"strength": "REQ-001"}, "fixed_faces": fixture.fixed(faces),
            "loads": [{"selector": fixture.load(faces), "force": force, "load": fixture.load_id}],
            "mesh_size": fixture.mesh_size, "timeout_seconds": 600}


class Specification(unittest.TestCase):
    """What every fixture promises, without FreeCAD or cadquery."""

    def test_each_fixture_has_a_unit_direction_and_a_positive_load(self) -> None:
        for fixture in FIXTURE_SET:
            with self.subTest(fixture=fixture.name):
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in fixture.direction)), 1.0, places=9)
                self.assertGreater(fixture.force_n, 0)
                self.assertGreater(fixture.mesh_size, 0)

    def test_names_and_part_numbers_are_unique_and_the_parts_exist(self) -> None:
        self.assertEqual(len({f.pn for f in FIXTURE_SET}), len(FIXTURE_SET))
        listed = json.loads(subprocess.run([UV, "run", "--script", str(FIXTURES / "fea_parts.py"), "--list"], capture_output=True,
                                           text=True, check=True).stdout) if UV else sorted(BY_NAME)
        self.assertEqual(sorted(BY_NAME), listed)

    def test_each_part_uses_a_profile_its_route_can_have(self) -> None:
        for fixture in FIXTURE_SET:
            with self.subTest(fixture=fixture.name):
                self.assertIn(fixture.material, PROFILES)
                moulded = PROFILES[fixture.material]["form"] == "moulded"
                self.assertEqual(moulded, fixture.route == "moulding")

    def test_the_record_names_every_load_with_its_direction_and_magnitude(self) -> None:
        loads = {load["id"]: load for load in documents()["product.yaml"]["use_case"]["loads"]}
        for fixture in FIXTURE_SET:
            self.assertEqual(loads[fixture.load_id]["direction"], list(fixture.direction))
            self.assertEqual(loads[fixture.load_id]["magnitude"]["value"]["value"], fixture.force_n)

    def test_the_case_force_is_the_service_load_along_its_direction(self) -> None:
        faces = [{"index": 1, "kind": "Cylinder", "radius": 9.0, "centre": [0, 0, 0]}]
        for fixture in FIXTURE_SET:
            with self.subTest(fixture=fixture.name):
                if fixture.name in ("pillow_block", "pipe_flange", "sprocket"):
                    continue   # these select cylinders or flanks by index, resolved against the real faces
                case = case_yaml(fixture, faces)
                force = case["loads"][0]["force"]
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in force)), fixture.force_n, places=6)
                self.assertEqual([c / fixture.force_n for c in force], list(fixture.direction))

    def test_the_project_record_is_valid(self) -> None:
        import fea_conditions

        with tempfile.TemporaryDirectory() as tmp:
            hw = Path(tmp) / "hw"
            lay_out(hw)
            record, _ = fea_conditions.read_record(hw / "versions" / "v1" / "record")
            self.assertEqual(sorted(record.parts), sorted(f.pn for f in FIXTURE_SET))
            self.assertEqual(sorted(record.materials), sorted(PROFILES))


@unittest.skipUnless(FREECAD_PRESENT and UV, "the solves need FreeCAD 1.1.4 freecadcmd and uv (for cadquery)")
class Solves(unittest.TestCase):
    """Each fixture is built, its faces resolved in FreeCAD's numbering, and solved once; it publishes a bundle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="cad-fea-parts-")
        cls.root = Path(cls.tmp.name)
        cls.hw = cls.root / "project" / "hw"
        lay_out(cls.hw)
        cls.summary: dict[str, dict[str, Any]] = {}

    @classmethod
    def tearDownClass(cls) -> None:
        print("\nFEA part fixtures:", json.dumps(cls.summary, sort_keys=True), file=sys.stderr)
        cls.tmp.cleanup()

    def build(self, fixture: Fixture) -> tuple[Path, dict[str, Any], Faces]:
        folder = self.hw / "analysis" / "cases" / fixture.name
        folder.mkdir(parents=True, exist_ok=True)
        step = folder / f"{fixture.name}.step"
        built = subprocess.run([UV, "run", "--script", str(FIXTURES / "fea_parts.py"), fixture.name, str(step), fixture.pn],
                               capture_output=True, text=True, timeout=600)
        self.assertEqual(built.returncode, 0, built.stderr[-1500:])
        info = json.loads(built.stdout.strip().splitlines()[-1])
        self.assertEqual(info["solids"], 1)
        faces_path = folder / "faces.json"
        listed = subprocess.run([str(FREECADCMD), str(FIXTURES / "list_faces.py")], capture_output=True, text=True, timeout=300,
                                env={**os.environ, "FEA_FACES_STEP": str(step), "FEA_FACES_OUT": str(faces_path)})
        self.assertTrue(faces_path.is_file(), listed.stderr[-1500:] + listed.stdout[-1500:])
        faces = json.loads(faces_path.read_text(encoding="utf-8"))
        faces_path.unlink()
        return folder, info, faces

    def solve(self, name: str) -> None:
        fixture = BY_NAME[name]
        folder, info, faces = self.build(fixture)
        box = info["bbox_mm"]
        sides = [box[3] - box[0], box[4] - box[1], box[5] - box[2]]
        aspect = max(sides) / min(sides)
        self.assertAlmostEqual(aspect, fixture.aspect, delta=0.05 * fixture.aspect)
        path = folder / "case.yaml"
        path.write_text(yaml.safe_dump(case_yaml(fixture, faces), sort_keys=False), encoding="utf-8")
        output = io.StringIO()
        with patch("sys.stdout", output):
            status = fea_static.main([str(path), "--hw", str(self.hw), "--version", "v1"])
        report = json.loads(output.getvalue())
        self.assertIn(status, (0, 1), report)   # 1 is a missed margin: the bundle is published all the same
        bundle = self.hw / "analysis" / "v1" / fixture.name
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        results = json.loads((bundle / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("status"), report.get("status"), manifest)
        self.assertTrue(results["max_von_mises_mpa"] > 0 and results["max_displacement_mm"] > 0, results)
        self.assertEqual(report["material"]["source"], "profile")
        self.assertEqual(report["binding"]["material_profile"]["id"], fixture.material)
        self.__class__.summary[name] = {"dof": 3 * results["node_count"], "nodes": results["node_count"],
                                        "elements": results["element_count"], "aspect": round(aspect, 2),
                                        "bbox_mm": sides, "max_von_mises_mpa": round(results["max_von_mises_mpa"], 2),
                                        "safety_factor_yield": round(results["safety_factor_yield"], 3),
                                        "solve_s": round(results["solve_time_seconds"], 1), "mesh_size_mm": fixture.mesh_size}


def _solve_test(name: str) -> Callable[[Solves], None]:
    def test(self: Solves) -> None:
        self.solve(name)
    test.__doc__ = f"{BY_NAME[name].title}: {BY_NAME[name].note}."
    return test


for _fixture in FIXTURE_SET:
    setattr(Solves, f"test_{_fixture.name}_builds_and_publishes_a_bundle", _solve_test(_fixture.name))


if __name__ == "__main__":
    unittest.main()
