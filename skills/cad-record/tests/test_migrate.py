#!/usr/bin/env python3
"""migrate.py: record version 1 to 2. A dry run reports the exact changes and writes nothing; --apply
adds explicit unknowns and open questions, moves drawing.operations to top-level operations, writes a
migration note, and leaves model.py, geometry, versions, releases, bundles, decisions, BOM and
verification untouched. It is idempotent, crash-safe and refuses what it cannot migrate faithfully."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
MIGRATE = SKILL / "scripts" / "migrate.py"
VALIDATOR = SKILL / "scripts" / "validate.py"
TEMPLATE = SKILL / "assets" / "project-template" / "hw"


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402


fx = load("cad_record_mi_fixtures", HERE / "fixtures" / "manufacturing_intent.py")
mi = fx.intent_module()
migrate = fx.migrate_module()
TODAY = date(2026, 1, 2)


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class MigrationCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-migrate-test-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project"
        self.hw = self.project / "hw"
        shutil.copytree(fx.LAMP, self.hw)

    def run_cli(self, *args: str) -> tuple[int, Any]:
        result = subprocess.run([sys.executable, str(MIGRATE), str(self.project), *args], capture_output=True, text=True,
                                timeout=120, check=False)
        output = result.stdout if result.stdout.strip() else result.stderr
        return result.returncode, (json.loads(output) if "--json" in args and result.stdout.strip() else output)

    def edit_yaml(self, rel: str, change: Any) -> None:
        path = self.hw / rel
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class LampExample(MigrationCase):
    def test_dry_run_is_the_default_and_reports_what_no_flag_reports(self) -> None:
        before = tree(self.project)
        self.assertEqual(self.run_cli("--dry-run", "--json"), self.run_cli("--json"))
        self.assertEqual(tree(self.project), before)

    def test_a_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.run_cli("--apply")[0], 0)
        migrated = tree(self.project)
        for args in (("--apply", "--json"), ("--json",)):
            with self.subTest(args=args):
                code, report = self.run_cli(*args)
                self.assertEqual((code, report["status"], report["changes"]), (0, "current", []))
                self.assertEqual(tree(self.project), migrated)

    def test_sealed_versions_releases_bundles_and_geometry_are_never_touched(self) -> None:
        write(self.hw / "versions" / "v1" / "record" / "product.yaml", "id: lamp-arm\n")
        write(self.hw / "versions" / "v1" / "manifest.json", json.dumps({"label": "v1", "files": {}}))
        write(self.hw / "releases" / "A" / "manifest.json", "{}")
        write(self.hw / "checks" / "v1" / "LMP-0001" / "result.json", "{}")
        write(self.hw / "parts" / "LMP-0005" / "sources" / "quote.pdf", "%PDF quote\n")
        sealed = {rel: digest for rel, digest in tree(self.hw).items() if rel.split("/")[0] in ("versions", "releases", "checks")
                  or "/sources/" in rel}
        migrate.apply(migrate.plan(self.hw, TODAY))
        self.assertEqual({rel: digest for rel, digest in tree(self.hw).items() if rel in sealed}, sealed)
        self.assertFalse((self.project / ".cad-work" / "migration").exists() and any((self.project / ".cad-work" / "migration").iterdir()),
                         "staging is cleaned up")


class Refusals(MigrationCase):
    def assert_refused(self, expected: str) -> None:
        before = tree(self.project)
        code, report = self.run_cli("--apply", "--json")
        self.assertEqual((code, report["status"]), (2, "refused"))
        self.assertIn(expected, report["message"])
        self.assertEqual(tree(self.project), before, "a refusal writes nothing")

    def test_an_unknown_record_version_is_refused(self) -> None:
        self.edit_yaml("product.yaml", lambda d: d.update(record_version=3))
        self.assert_refused("record_version 3 is not supported")

    def test_dry_run_and_apply_are_exclusive(self) -> None:
        before = tree(self.project)
        code, message = self.run_cli("--dry-run", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("not allowed with argument", message)
        self.assertEqual(tree(self.project), before)


class EdgeCases(MigrationCase):
    def test_the_project_template_is_already_current(self) -> None:
        shutil.rmtree(self.hw)
        shutil.copytree(TEMPLATE, self.hw)
        self.assertEqual(migrate.plan(self.hw, TODAY).status, "current")

    def test_crlf_files_keep_their_newlines(self) -> None:
        path = self.hw / "parts" / "LMP-0003" / "part.yaml"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        migrate.apply(migrate.plan(self.hw, TODAY))
        data = path.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"intent:\r\n", data)

if __name__ == "__main__":
    unittest.main()
