#!/usr/bin/env python3
"""The 3MF writer and reader (cadmodel.threemf): package layout, deterministic bytes, structure checks and
the mesh measures. The tests that need trimesh, lxml, lib3mf or OCP run in uv scripts (fixtures/threemf_probe.py,
fixtures/threemf_occ_probe.py).

Run: python3 -m unittest discover -s cad/skills/cad-model/tests -p test_threemf.py -v
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent / "scripts"))
from cadmodel import threemf  # noqa: E402

# A 10 mm cube: 8 vertices, 12 triangles wound outward. Its volume is 10^3 and its area 6 x 10^2.
CUBE_VERTICES = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0),
                 (0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0)]
CUBE_TRIANGLES = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                  (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
ORDER = ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"]


def cube(material: str | None = "PETG") -> bytes:
    return threemf.dumps(CUBE_VERTICES, CUBE_TRIANGLES, name="TST-0001", material=material)


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def repack(data: bytes, **replace: bytes) -> bytes:
    """`data` with the named entries' bytes swapped, in the same order."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, replace.get(info.filename, source.read(info.filename)))
    return out.getvalue()


class PackageTests(unittest.TestCase):
    def test_entries_are_in_the_fixed_order_stored_and_dated_1980(self) -> None:
        with zipfile.ZipFile(io.BytesIO(cube())) as archive:
            infos = archive.infolist()
        self.assertEqual([info.filename for info in infos], ORDER)
        for info in infos:
            with self.subTest(entry=info.filename):
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual((info.create_system, info.external_attr), (3, 0o644 << 16))

    def test_two_writes_of_the_same_mesh_are_byte_identical(self) -> None:
        self.assertEqual(cube(), cube())

    def test_a_different_material_changes_the_bytes_and_none_writes_no_metadata(self) -> None:
        self.assertNotEqual(cube("PETG"), cube("PLA"))
        model = entries(cube(None))["3D/3dmodel.model"].decode("utf-8")
        self.assertNotIn("metadata", model)

    def test_the_model_is_core_millimetres_with_one_object_in_the_build_and_no_extension(self) -> None:
        model = entries(cube())["3D/3dmodel.model"].decode("utf-8")
        self.assertIn('unit="millimeter"', model)
        self.assertIn('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"', model)
        self.assertEqual(model.count("<object "), 1)
        self.assertEqual(model.count("<item "), 1)
        for absent in ("production", "UUID", "requiredextensions", "slice", "toolpath", "displaycolor", "basematerials",
                       "pid="):
            self.assertNotIn(absent, model)

    def test_a_float_is_written_with_six_decimals_and_no_negative_zero(self) -> None:
        data = threemf.dumps([(-0.0, 1 / 3, -2.5e-9), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [(0, 1, 2)], name="P")
        model = entries(data)["3D/3dmodel.model"].decode("utf-8")
        self.assertIn('<vertex x="0.000000" y="0.333333" z="0.000000"/>', model)

    def test_a_name_and_material_with_markup_are_escaped(self) -> None:
        data = threemf.dumps(CUBE_VERTICES, CUBE_TRIANGLES, name='A&"<B>', material="PLA <1> & \"2\"")
        read = threemf.read(data).objects[0]
        self.assertEqual((read.name, read.metadata), ('A&"<B>', {"cadagent:material": 'PLA <1> & "2"'}))


class ReadTests(unittest.TestCase):
    def test_the_mesh_the_writer_wrote_is_read_back(self) -> None:
        model = threemf.read(cube())
        (obj,) = model.objects
        self.assertEqual((model.unit, obj.id, obj.name, obj.metadata), ("millimeter", 1, "TST-0001",
                                                                       {"cadagent:material": "PETG"}))
        self.assertEqual(obj.vertices, CUBE_VERTICES)
        self.assertEqual(obj.triangles, CUBE_TRIANGLES)
        self.assertEqual([(item.objectid, item.transform) for item in model.build], [(1, None)])

    def test_a_package_that_breaks_the_structure_is_refused(self) -> None:
        good = entries(cube())
        model = good["3D/3dmodel.model"].decode("utf-8")
        cases = {
            "build item names a missing object": repack(cube(), **{"3D/3dmodel.model": model.replace(
                '<item objectid="1"', '<item objectid="7"').encode()}),
            "triangle index past the vertices": repack(cube(), **{"3D/3dmodel.model": model.replace(
                'v2="2" v3="1"', 'v2="2" v3="99"', 1).encode()}),
            "unit is inch": repack(cube(), **{"3D/3dmodel.model": model.replace("millimeter", "inch").encode()}),
            "duplicate object id": repack(cube(), **{"3D/3dmodel.model": model.replace(
                "</resources>", model[model.index("<object "):model.index("</resources>")] + "</resources>").encode()}),
            "model is not XML": repack(cube(), **{"3D/3dmodel.model": b"not xml"}),
            "content types lack the model": repack(cube(), **{"[Content_Types].xml": b'<Types xmlns="http://schemas.'
                b'openxmlformats.org/package/2006/content-types"/>'}),
            "relationships lack the model": repack(cube(), **{"_rels/.rels": b'<Relationships xmlns="http://schemas.'
                b'openxmlformats.org/package/2006/relationships"/>'}),
            "extra entry": _with_extra(cube()),
            "not a zip": b"PK nothing",
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(threemf.ThreeMFError):
                threemf.read(data)


def _with_extra(data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info.filename))
        target.writestr("extra.txt", b"x")
    return out.getvalue()


class MeasureTests(unittest.TestCase):
    def measure(self, triangles: list[tuple[int, int, int]], vertices=CUBE_VERTICES) -> dict:
        return threemf.measure(vertices, triangles)

    def test_the_cube_is_closed_wound_outward_and_measured(self) -> None:
        report = self.measure(CUBE_TRIANGLES)
        self.assertEqual((report["watertight"], report["winding_consistent"]), (True, True))
        self.assertAlmostEqual(report["volume_mm3"], 1000.0)
        self.assertAlmostEqual(report["area_mm2"], 600.0)
        self.assertEqual(report["bbox_mm"], {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]})

    def test_a_missing_triangle_is_a_crack(self) -> None:
        report = self.measure(CUBE_TRIANGLES[:-1])
        self.assertEqual(report["watertight"], False)

    def test_one_reversed_triangle_breaks_the_winding(self) -> None:
        flipped = [*CUBE_TRIANGLES[:-1], CUBE_TRIANGLES[-1][::-1]]
        report = self.measure(flipped)
        self.assertEqual((report["watertight"], report["winding_consistent"]), (True, False))

    def test_a_mesh_wound_inward_has_negative_volume(self) -> None:
        report = self.measure([triangle[::-1] for triangle in CUBE_TRIANGLES])
        self.assertEqual((report["watertight"], report["winding_consistent"]), (True, True))
        self.assertAlmostEqual(report["volume_mm3"], -1000.0)

    def test_a_vertex_duplicated_in_place_leaves_the_mesh_open(self) -> None:
        vertices = [*CUBE_VERTICES, CUBE_VERTICES[0]]
        triangles = [(8, 2, 1), *CUBE_TRIANGLES[1:]]  # the first triangle uses a copy of vertex 0
        self.assertEqual(self.measure(triangles, vertices)["watertight"], False)


@unittest.skipUnless(shutil.which("uv"), "uv is required for the trimesh, lxml and lib3mf environment")
class ReadBackTests(unittest.TestCase):
    """Other readers' view of a written package: trimesh, the 3MF Consortium schemas and lib3mf."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        target = Path(cls.tmp.name) / "part.3mf"
        target.write_bytes(cube())
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(FIXTURES / "threemf_probe.py"), str(target)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr[-3000:]
        cls.report = json.loads(proc.stdout)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_trimesh_reads_one_closed_millimetre_object_at_the_identity(self) -> None:
        trimesh = self.report["trimesh"]
        self.assertEqual(trimesh["units"], "millimeter")
        self.assertEqual(trimesh["geometries"], 1)
        self.assertEqual(trimesh["transform"], [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        self.assertTrue(trimesh["watertight"])
        self.assertAlmostEqual(trimesh["volume_mm3"], 1000.0)

    def test_every_xml_part_validates_against_the_3mf_consortium_schemas(self) -> None:
        self.assertEqual(self.report["schema"], {"[Content_Types].xml": [], "_rels/.rels": [], "3D/3dmodel.model": []})

    def test_lib3mf_reads_it_in_strict_mode_where_a_wheel_exists(self) -> None:
        lib3mf = self.report["lib3mf"]
        if lib3mf.get("skipped"):
            self.skipTest(lib3mf["skipped"])
        self.assertEqual(lib3mf, {"warnings": 0, "meshes": 1, "vertices": 8, "triangles": 12})


if __name__ == "__main__":
    unittest.main()
