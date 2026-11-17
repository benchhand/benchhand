#!/usr/bin/env python3
"""The content-addressed store under hw/.cad/cache/, and layout's input_key, code_shas and tool_versions."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, filename: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
        assert spec and spec.loader
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


layout = _load("cad_record_layout", "layout.py")
store = _load("cad_record_store", "store.py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key(name: str) -> str:
    """A store key for a test: any sha256."""
    return sha(name.encode())


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-record-store-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hw = self.root / "hw"
        self.hw.mkdir()
        self.store = store.Store(self.hw)

    def file(self, name: str, data: bytes) -> Path:
        path = self.root / "out" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def claimed(self, key: str, members: dict[str, bytes], **fields) -> dict:
        record = {"kind": "test", "members": {name: self.store.intern(self.file(name, data))
                                              for name, data in members.items()}, **fields}
        self.assertEqual(self.store.claim(key, record), "claimed")
        return record


class InputKey(unittest.TestCase):
    def test_the_key_is_the_canonical_recipe_sha_of_the_allowlisted_inputs(self) -> None:
        material = {"format": 2, "kind": "part", "inputs": {"model.py": "ab" * 32}, "kernel": "cadquery"}
        self.assertEqual(layout.input_key(material), layout.recipe_sha256(material))
        self.assertEqual(layout.input_key(material, allow=("format", "kind", "inputs", "kernel")),
                         layout.recipe_sha256(material))

    def test_a_key_needs_its_format_and_kind_and_only_allowlisted_inputs(self) -> None:
        for material in ({"kind": "part"}, {"format": 1}, {"format": 1, "kind": ""}):
            with self.subTest(material=material), self.assertRaises(ValueError):
                layout.input_key(material)
        with self.assertRaisesRegex(ValueError, "build_s"):  # a timing is never a key input
            layout.input_key({"format": 1, "kind": "part", "build_s": 0.2}, allow=("format", "kind"))

    def test_code_shas_and_tool_versions_name_explicit_files_and_distributions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_bytes(b"a")
            (root / "sub").mkdir()
            (root / "sub" / "b.py").write_bytes(b"b")
            self.assertEqual(layout.code_shas(root, ["sub/b.py", "a.py"]), {"a.py": sha(b"a"), "sub/b.py": sha(b"b")})
        versions = layout.tool_versions(("pyyaml", "no-such-distribution-x"))
        self.assertRegex(versions["pyyaml"], r"^\d+\.")
        self.assertEqual(versions["no-such-distribution-x"], "missing")


class Objects(Fixture):
    def test_an_object_is_stored_once_read_only_under_its_sha(self) -> None:
        first = self.store.intern(self.file("a.step", b"STEP"))
        second = self.store.intern(self.file("b.step", b"STEP"))
        self.assertEqual(first, second)
        self.assertEqual(first, sha(b"STEP"))
        stored = self.hw / ".cad" / "cache" / "objects" / first
        self.assertEqual(stored.read_bytes(), b"STEP")
        self.assertEqual(stored.stat().st_mode & 0o777, 0o444)
        self.assertEqual(layout.classify(stored, self.hw).entry.id, "cache.part_objects")

    def test_place_object_links_or_copies_read_only_and_misses_a_removed_object(self) -> None:
        digest = self.store.intern(self.file("a.step", b"STEP"))
        target = self.root / "run" / "a.step"
        self.assertTrue(self.store.place_object(digest, target))
        self.assertEqual((target.read_bytes(), target.stat().st_mode & 0o777), (b"STEP", 0o444))
        os.chmod(self.hw / ".cad" / "cache" / "objects" / digest, 0o644)
        (self.hw / ".cad" / "cache" / "objects" / digest).unlink()
        self.assertFalse(self.store.place_object(digest, self.root / "run" / "b.step"))
        self.assertFalse((self.root / "run" / "b.step").exists())


class Records(Fixture):
    def test_a_claimed_record_is_read_back_with_its_members(self) -> None:
        record = self.claimed(key("k"), {"mesh.unv": b"UNV", "sub/mesh.json": b"{}"}, facts={"nodes": 3})
        found = self.store.record(key("k"))
        self.assertEqual((found.reason, found.record["members"], found.record["facts"]),
                         ("", record["members"], {"nodes": 3}))
        self.assertEqual(layout.classify(self.hw / ".cad" / "cache" / "records" / f"{key('k')}.json", self.hw).entry.id,
                         "cache.part_records")

    def test_a_missing_record_is_a_miss_and_a_changed_object_is_stale_with_its_reason(self) -> None:
        self.assertTrue(self.store.record(key("m")).reason.startswith("miss"))
        record = self.claimed(key("s"), {"mesh.unv": b"UNV"})
        path = self.hw / ".cad" / "cache" / "objects" / record["members"]["mesh.unv"]
        os.chmod(path, 0o644)
        path.write_bytes(b"CHANGED")  # an accidental in-place write
        found = store.Store(self.hw).record(key("s"))
        self.assertIsNone(found.record)
        self.assertRegex(found.reason, r"^stale: mesh\.unv .* does not hash to its name")

    def test_the_first_writer_of_a_key_wins_and_a_different_second_record_is_a_finding(self) -> None:
        first = self.claimed(key("w"), {"mesh.unv": b"ONE"})
        same = {"kind": "test", "members": dict(first["members"])}
        self.assertEqual(self.store.claim(key("w"), same), "exists")
        other = {"kind": "test", "members": {"mesh.unv": self.store.intern(self.file("x.unv", b"TWO"))}}
        outcome = self.store.claim(key("w"), other)
        self.assertRegex(outcome, r"^conflict: .*mesh\.unv.*the key is missing an input")
        self.assertEqual(self.store.record(key("w")).record["members"], first["members"])
        findings = (self.hw / ".cad" / "cache" / "findings.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(findings[-1])["key"], key("w"))

    def test_concurrent_writers_of_one_key_leave_exactly_one_record(self) -> None:
        records = [{"kind": "test", "members": {"m": self.store.intern(self.file(f"{i}", str(i).encode()))}}
                   for i in range(8)]
        outcomes: list[str] = []
        threads = [threading.Thread(target=lambda r=r: outcomes.append(store.Store(self.hw).claim(key("c"), r)))
                   for r in records]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("claimed"), 1)
        self.assertEqual(sum(o.startswith("conflict") for o in outcomes), 7)

    def test_a_record_that_names_a_version_is_refused(self) -> None:
        digest = self.store.intern(self.file("a", b"A"))
        for members in ({"versions/v3/mesh.unv": digest}, {"v3.unv": digest}):
            with self.subTest(members=members), self.assertRaisesRegex(ValueError, "version label v3"):
                self.store.claim(key("v"), {"kind": "test", "members": members}, version_labels=("v3",))
        self.assertEqual(self.store.claim(key("v"), {"kind": "test", "members": {"v30x.unv": digest}},
                                          version_labels=("v3",)), "claimed")

    def test_a_record_member_must_be_a_plain_relative_path(self) -> None:
        digest = self.store.intern(self.file("a", b"A"))
        for name in ("/abs", "../up", "a/../b", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.claim(key("p"), {"kind": "test", "members": {name: digest}})


class Place(Fixture):
    def test_a_record_is_laid_down_whole_and_replaces_an_existing_destination_whole(self) -> None:
        record = self.claimed(key("d"), {"mesh.unv": b"UNV", "sub/mesh.json": b"{}"})
        destination = self.root / "run" / "mesh"
        destination.mkdir(parents=True)
        (destination / "old.txt").write_text("old", encoding="utf-8")
        self.assertEqual(self.store.place(record, destination), "")
        self.assertEqual(sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()),
                         ["mesh.unv", "sub/mesh.json"])
        self.assertEqual((destination / "mesh.unv").read_bytes(), b"UNV")
        self.assertEqual([p.name for p in destination.parent.iterdir()], ["mesh"])  # no staging left beside it

    def test_a_record_with_a_missing_object_leaves_the_destination_untouched(self) -> None:
        record = self.claimed(key("u"), {"a": b"A", "b": b"B"})
        missing = self.hw / ".cad" / "cache" / "objects" / record["members"]["b"]
        os.chmod(missing, 0o644)
        missing.unlink()
        destination = self.root / "run" / "mesh"
        reason = self.store.place(record, destination)
        self.assertRegex(reason, r"^stale: b ")
        self.assertFalse(destination.exists())
        self.assertEqual(list((self.root / "run").iterdir()), [])


class Locks(Fixture):
    def test_the_lock_of_a_key_is_held_by_one_holder_at_a_time(self) -> None:
        held: list[str] = []
        with self.store.lock(key("l")):
            thread = threading.Thread(target=lambda: (self.store.lock(key("l")).__enter__(), held.append("second")))
            thread.start()
            thread.join(0.3)
            self.assertEqual(held, [])
        thread.join(5)
        self.assertEqual(held, ["second"])
        self.assertEqual(layout.classify(self.hw / ".cad" / "cache" / "locks" / f"{key('l')}.lock", self.hw).entry.id,
                         "cache.locks")


class Collection(Fixture):
    def seal(self, rel: str, files: dict[str, str]) -> None:
        path = self.hw / rel / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"files": files}), encoding="utf-8")

    def test_gc_keeps_what_any_sealed_manifest_names_and_removes_the_rest(self) -> None:
        kept = self.claimed(key("a"), {"part.step": b"VERSION"})
        bundle = self.claimed(key("b"), {"mesh.unv": b"BUNDLE"})
        released = self.claimed(key("c"), {"part.stl": b"RELEASE"})
        orphan = self.claimed(key("d"), {"x": b"ORPHAN"})
        self.seal("versions/v1", {"parts/P.step": kept["members"]["part.step"]})
        self.seal("analysis/v1/beam", {"evidence/mesh.unv": bundle["members"]["mesh.unv"]})
        self.seal("releases/A", {"P.stl": {"sha256": released["members"]["part.stl"], "original": "x"}})
        result = self.store.gc()
        self.assertEqual(result["objects"]["removed"], 1)
        self.assertEqual(result["records"]["removed"], 1)
        self.assertEqual(result["manifests"], 3)
        self.assertIsNotNone(self.store.record(key("b")).record)
        self.assertIsNone(self.store.record(key("d")).record)
        self.assertFalse((self.hw / ".cad" / "cache" / "objects" / orphan["members"]["x"]).exists())

    def test_gc_removes_the_part_store_of_the_former_layout(self) -> None:
        for folder in ("parts", "builds"):
            (self.hw / ".cad" / folder).mkdir(parents=True)
            (self.hw / ".cad" / folder / "x").write_text("old", encoding="utf-8")
            os.chmod(self.hw / ".cad" / folder / "x", 0o444)
        dry = self.store.gc(dry_run=True)
        self.assertEqual(dry["former_layout_removed"], [".cad/builds", ".cad/parts"])
        self.assertTrue((self.hw / ".cad" / "parts").is_dir())
        self.store.gc()
        self.assertFalse((self.hw / ".cad" / "parts").exists())
        self.assertFalse((self.hw / ".cad" / "builds").exists())

    def test_gc_refuses_an_unreadable_manifest(self) -> None:
        path = self.hw / "versions" / "v1" / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(store.StoreError, "manifest"):
            self.store.gc()


if __name__ == "__main__":
    unittest.main()
