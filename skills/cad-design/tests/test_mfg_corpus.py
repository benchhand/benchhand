#!/usr/bin/env python3
"""Tests for the mutation-corpus harness itself (mfg_corpus/): rate arithmetic, registry, the rates command.

Run: python3 -m unittest discover -s skills/cad-design/tests -p test_mfg_corpus.py
Pure standard library; the families' own checks run through mfg_corpus/rates.py.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "mfg_corpus"
sys.path.insert(0, str(CORPUS))
import corpus  # noqa: E402


def pair(ident: str, mutant: str, twin: str = "pass") -> corpus.Pair:
    return corpus.Pair(ident, "d", lambda is_mutant: mutant if is_mutant else twin)


def skipping(is_mutant: bool) -> str:
    raise corpus.Skip("no ocp")


def crashing(is_mutant: bool) -> str:
    raise RuntimeError("boom")


class RateTests(unittest.TestCase):
    def rows(self, *pairs: corpus.Pair) -> dict:
        return corpus.evaluate([corpus.Family("f", "chk", tuple(pairs))])[0]

    def test_rates_are_counted_per_variant(self) -> None:
        row = self.rows(pair("a", "fail"), pair("b", "fail"), pair("c", "unknown"), pair("d", "pass", "fail"))
        self.assertEqual((row["detection_rate"], row["abstention_rate"], row["false_positive_rate"]), (0.5, 0.25, 0.25))
        self.assertEqual((row["detected"], row["abstained"], row["missed"], row["false_positives"]), (2, 1, 1, 1))

    def test_skipped_pairs_leave_the_rates_and_are_counted(self) -> None:
        row = self.rows(pair("a", "fail"), corpus.Pair("b", "d", skipping))
        self.assertEqual((row["pairs"], row["ran"], row["skipped"], row["detection_rate"]), (2, 1, 1, 1.0))

    def test_a_crashing_check_is_a_miss_not_a_detection(self) -> None:
        row = self.rows(corpus.Pair("a", "d", crashing))
        self.assertEqual((row["errors"], row["detected"], row["detection_rate"]), (1, 0, 0.0))
        self.assertIn("boom", row["results"][0]["error"])

    def test_a_family_with_nothing_run_has_no_rate(self) -> None:
        row = self.rows(corpus.Pair("a", "d", skipping))
        self.assertIsNone(row["detection_rate"])

    def test_an_unknown_outcome_word_is_an_error(self) -> None:
        self.assertEqual(self.rows(pair("a", "maybe"))["errors"], 1)


class RegistryTests(unittest.TestCase):
    def test_duplicate_family_ids_and_pair_ids_are_rejected(self) -> None:
        one = corpus.Family("f", "c", (pair("a", "fail"),))
        with self.assertRaises(ValueError):
            corpus.validate([one, one])
        with self.assertRaises(ValueError):
            corpus.validate([corpus.Family("g", "c", (pair("a", "fail"), pair("a", "fail")))])

    def test_a_family_module_dropped_in_the_directory_is_registered_and_underscore_files_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mine.py").write_text(
                "from corpus import Family, Pair\nFAMILY = Family('mine', 'c', (Pair('p', 'd', lambda m: 'fail' if m else 'pass'),))\n")
            (Path(tmp) / "_skip.py").write_text("raise SystemExit('must not load')\n")
            self.assertEqual([f.id for f in corpus.load_families(Path(tmp))], ["mine"])

    def test_the_shipped_registry_loads_with_unique_ids(self) -> None:
        corpus.load_families()


class CommandTests(unittest.TestCase):
    def test_the_rate_command_writes_json_and_prints_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.json"
            done = subprocess.run([sys.executable, str(CORPUS / "rates.py"), "--out", str(out)], capture_output=True,
                                  text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(f"report: {out}", done.stdout)
            data = json.loads(out.read_text())
            self.assertEqual(data["format"], 1)
            self.assertTrue(all("detection_rate" in f for f in data["families"]))


DEPS = importlib.util.find_spec("yaml") is not None and importlib.util.find_spec("jsonschema") is not None


@unittest.skipUnless(DEPS, "pyyaml and jsonschema are required")
class ProfileTests(unittest.TestCase):
    def test_the_test_profile_is_a_valid_record_with_the_calibrations_the_additive_checks_read(self) -> None:
        import capability_profile

        sys.path.insert(0, str(CORPUS.parents[1] / "scripts"))
        import process_checks

        fx_path = CORPUS.parents[2] / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py"
        spec = importlib.util.spec_from_file_location("mfg_corpus_fx", fx_path)
        assert spec and spec.loader
        fx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fx)
        documents = fx.record(capabilities=capability_profile.capabilities(), materials=capability_profile.materials())
        with tempfile.TemporaryDirectory() as tmp:
            hw = fx.write_record(Path(tmp) / "project" / "hw", documents)
            _, problems = process_checks.intent().check_record(hw)
        self.assertEqual([p.code for p in problems if p.severity == "error"], [])
        found = {c["characteristic"] for c in capability_profile.capabilities()[0]["calibrations"]}
        self.assertEqual(found, {"overhang_angle", "bridge_span", "min_wall", "min_feature"})


if __name__ == "__main__":
    unittest.main()
