#!/usr/bin/env python3
"""emit.py --sarif: the tool-result envelope's findings exported as SARIF 2.1.0.

Run: python3 -m unittest discover -s skills/cad-record/tests -p test_sarif.py -v
Needs jsonschema (the CI test pin) for the validation tests only; export itself is standard library.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SKILLS = SKILL.parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from modload import load_module  # noqa: E402

emit = load_module("cad_record_emit", SKILL / "scripts" / "emit.py")
sarif = load_module("cad_record_sarif", SKILL / "scripts" / "sarif.py")
VENDORED = SKILL / "vendor" / "sarif-schema-2.1.0.json"
PIN = SKILL / "vendor" / "sarif-schema-2.1.0.json.pin"
PRODUCER_FINDINGS = HERE / "fixtures" / "producer_findings.py"
PINNED_SHA256 = "c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e"


def producer_shapes() -> dict[str, dict[str, Any]]:
    """Findings built by the code of each producer (see fixtures/producer_findings.py), run in a subprocess."""
    run = subprocess.run([sys.executable, "-B", str(PRODUCER_FINDINGS)], capture_output=True, text=True, check=False)
    if run.returncode != 0:
        raise RuntimeError(f"{PRODUCER_FINDINGS.name} exited {run.returncode}: {run.stderr[-2000:]}")
    return json.loads(run.stdout)  # type: ignore[no-any-return]


# form_check builds its findings inline and needs FreeCAD. These literals match test_schema_finding.FORM_CHECK_FINDINGS.
FORM_CHECK = {
    "min_wall": {"check": "min_wall", "part": "TST-0001", "thickness_mm": 0.4, "location_mm": [1.0, 2.0, 3.0],
                 "min_mm": 0.8, "blocking": False},
    "brep_invalid": {"check": "brep_invalid", "part": "TST-0001", "source": "step", "blocking": True},
}
# Legacy shapes from validate.py:71 and the layout audit, before the severity migration.
LEGACY = {
    "validate": {"path": "hw/product.yaml", "message": "id is required", "severity": "error"},
    "validate_warning": {"path": "hw/stray.txt", "message": "a path no layout entry accepts", "severity": "warning",
                         "code": "layout.stray"},
}
FULL = {
    "code": "process.fdm.overhang", "severity": "major", "outcome": "fail",
    "subject": {"PN": "TST-0001", "feature": "mount_slot"},
    "location": {"file": "hw/parts/TST-0001/part.yaml", "line": 12},
    "message": "the overhang exceeds the printable angle", "remedy": "add a chamfer or split the part",
}


def schema_errors(document: Any) -> list[str]:
    return sarif.validation_errors(document)


def results_of(findings: list[Any], hw: Path | None = None) -> list[dict[str, Any]]:
    document = sarif.document(findings, hw=hw)
    return document["runs"][0]["results"]  # type: ignore[no-any-return]


class VendoredSchemaTests(unittest.TestCase):
    def test_the_vendored_schema_is_the_pinned_bytes(self) -> None:
        self.assertEqual(hashlib.sha256(VENDORED.read_bytes()).hexdigest(), PINNED_SHA256)

    def test_the_pin_file_records_the_same_hash_and_the_source_url(self) -> None:
        pin = PIN.read_text(encoding="utf-8")
        self.assertIn(f"sha256: {PINNED_SHA256}", pin)
        self.assertIn("url: https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/"
                      "sarif-schema-2.1.0.json", pin)

    def test_the_validator_rejects_a_document_that_is_not_sarif(self) -> None:
        self.assertTrue(schema_errors({"version": "2.0.0", "runs": []}))
        self.assertTrue(schema_errors({"version": "2.1.0", "runs": [{"tool": {}}]}))


class ValidDocumentTests(unittest.TestCase):
    def test_an_empty_findings_list_is_a_valid_run_with_no_results(self) -> None:
        document = sarif.document([])
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(document["version"], "2.1.0")
        self.assertEqual(document["runs"][0]["results"], [])

    def test_every_real_producer_shape_exports_to_a_valid_document(self) -> None:
        findings = [*producer_shapes().values(), *FORM_CHECK.values(), *LEGACY.values(), FULL]
        document = sarif.document(findings)
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(len(document["runs"][0]["results"]), len(findings))

    def test_a_document_with_a_resolved_part_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hw = Path(tmp) / "hw"
            (hw / "parts" / "TST-0001").mkdir(parents=True)
            (hw / "parts" / "TST-0001" / "part.yaml").write_text("pn: TST-0001\n", encoding="utf-8")
            self.assertEqual(schema_errors(sarif.document([FULL, *producer_shapes().values()], hw=hw)), [])


class MappingTests(unittest.TestCase):
    def test_a_full_finding_maps_field_by_field(self) -> None:
        [result] = results_of([FULL])
        self.assertEqual(result["ruleId"], "process.fdm.overhang")
        self.assertEqual(result["level"], "error")
        self.assertEqual(result["kind"], "fail")
        self.assertEqual(result["message"], {"text": "the overhang exceeds the printable angle"})
        self.assertEqual(result["properties"]["remedy"], "add a chamfer or split the part")
        self.assertEqual(result["properties"]["severity"], "major")
        self.assertEqual(result["properties"]["finding"], FULL)

    def test_the_canonical_severities_map_to_levels(self) -> None:
        for word, level in [("critical", "error"), ("major", "error"), ("minor", "warning"), ("info", "note")]:
            with self.subTest(severity=word):
                [result] = results_of([{"code": "x", "message": "m", "severity": word}])
                self.assertEqual(result["level"], level)
                self.assertNotIn("unrecognizedSeverity", result["properties"])

    def test_the_legacy_severities_map_by_their_blocking_effect_today(self) -> None:
        for word, level in [("error", "error"), ("warning", "warning"), ("stage", "warning")]:
            with self.subTest(severity=word):
                [result] = results_of([{"code": "x", "message": "m", "severity": word}])
                self.assertEqual(result["level"], level)
                self.assertEqual(result["properties"]["severity"], word)
                self.assertNotIn("unrecognizedSeverity", result["properties"])

    def test_no_severity_follows_the_blocking_flag_else_counts_as_major(self) -> None:
        cases = [({"blocking": True}, "error"), ({"blocking": False}, "warning"), ({}, "error")]
        for extra, level in cases:
            with self.subTest(extra=extra):
                [result] = results_of([{"code": "x", "message": "m", **extra}])
                self.assertEqual(result["level"], level)
                self.assertNotIn("severity", result["properties"])

    def test_an_unknown_severity_word_is_kept_flagged_and_reported_as_a_warning(self) -> None:
        [result] = results_of([{"code": "x", "message": "m", "severity": "catastrophic"}])
        self.assertEqual(result["level"], "warning")
        self.assertEqual(result["properties"]["severity"], "catastrophic")
        self.assertTrue(result["properties"]["unrecognizedSeverity"])

    def test_a_non_string_severity_is_flagged_not_a_crash(self) -> None:
        [result] = results_of([{"code": "x", "message": "m", "severity": ["major"]}])
        self.assertEqual(result["level"], "warning")
        self.assertTrue(result["properties"]["unrecognizedSeverity"])

    def test_outcome_maps_to_kind_and_a_non_failure_has_no_level(self) -> None:
        cases = [("fail", "fail", "error"), ("unknown", "review", "error"), ("pass", "pass", "none"),
                 ("not_applicable", "notApplicable", "none")]
        for outcome, kind, level in cases:
            with self.subTest(outcome=outcome):
                [result] = results_of([{"code": "x", "message": "m", "severity": "major", "outcome": outcome}])
                self.assertEqual((result["kind"], result["level"]), (kind, level))

    def test_no_outcome_or_an_unknown_one_leaves_kind_out(self) -> None:
        for extra in ({}, {"outcome": "maybe"}):
            with self.subTest(extra=extra):
                [result] = results_of([{"code": "x", "message": "m", **extra}])
                self.assertNotIn("kind", result)

    def test_one_rule_per_distinct_code_in_first_seen_order_with_the_first_remedy_as_help(self) -> None:
        findings = [{"code": "b.two", "message": "m1", "remedy": "fix b"}, {"code": "a.one", "message": "m2"},
                    {"code": "b.two", "message": "m3", "remedy": "fix b differently"}]
        document = sarif.document(findings)
        rules = document["runs"][0]["tool"]["driver"]["rules"]
        self.assertEqual([rule["id"] for rule in rules], ["b.two", "a.one"])
        self.assertEqual(rules[0]["help"], {"text": "fix b", "markdown": "fix b"})
        self.assertNotIn("help", rules[1])
        self.assertEqual([r["ruleId"] for r in document["runs"][0]["results"]], ["b.two", "a.one", "b.two"])
        self.assertEqual(document["runs"][0]["results"][2]["properties"]["remedy"], "fix b differently")

    def test_each_result_names_its_rule_by_index(self) -> None:
        document = sarif.document([{"code": "b", "message": "m"}, {"code": "a", "message": "m"}, {"code": "b", "message": "m"}])
        self.assertEqual([r["ruleIndex"] for r in document["runs"][0]["results"]], [0, 1, 0])

    def test_form_check_findings_use_check_as_the_rule_and_part_as_the_part(self) -> None:
        [result] = results_of([FORM_CHECK["brep_invalid"]])
        self.assertEqual(result["ruleId"], "brep_invalid")
        self.assertEqual(result["level"], "error")
        self.assertEqual(result["locations"][0]["logicalLocations"][0]["fullyQualifiedName"], "TST-0001")
        self.assertEqual(result["properties"]["finding"]["source"], "step")

    def test_the_driver_names_the_tool_and_the_run_keeps_the_envelope_status(self) -> None:
        document = sarif.document([FULL], tool="form_check", status="fail")
        run = document["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "form_check")
        self.assertEqual(run["properties"]["status"], "fail")


class HonestMistakeTests(unittest.TestCase):
    def test_a_finding_without_a_code_takes_check_id_or_rule_else_a_fixed_placeholder(self) -> None:
        cases = [({"check": "min_wall"}, "min_wall"), ({"id": "FC-1"}, "FC-1"), ({"rule": "r1"}, "r1"),
                 ({}, sarif.UNCODED)]
        for extra, rule in cases:
            with self.subTest(extra=extra):
                [result] = results_of([{"message": "m", **extra}])
                self.assertEqual(result["ruleId"], rule)

    def test_a_finding_without_a_message_gets_a_synthesised_one_naming_rule_and_part(self) -> None:
        [result] = results_of([FORM_CHECK["min_wall"]])
        self.assertEqual(result["message"]["text"], "min_wall: TST-0001 (the producer gave no message)")
        [bare] = results_of([{"code": "x"}])
        self.assertEqual(bare["message"]["text"], "x (the producer gave no message)")

    def test_a_finding_that_is_not_an_object_is_exported_as_uncoded_text(self) -> None:
        results = results_of(["rule-1: too steep", 7, None])
        self.assertEqual([r["ruleId"] for r in results], [sarif.UNCODED] * 3)
        self.assertEqual(results[0]["message"]["text"], "rule-1: too steep")
        self.assertEqual(schema_errors(sarif.document(["rule-1: too steep", 7, None])), [])

    def test_no_finding_is_dropped(self) -> None:
        findings = [{}, {"code": "x", "message": "m"}, "text", {"code": "x", "message": "m"}]
        self.assertEqual(len(results_of(findings)), 4)

    def test_a_part_with_no_resolvable_file_has_no_physical_location_but_keeps_the_logical_one(self) -> None:
        for hw in (None, Path(tempfile.gettempdir()) / "no-such-hw-dir"):
            with self.subTest(hw=hw):
                [result] = results_of([FULL | {"location": None}], hw=hw)
                self.assertEqual(result["locations"], [{"logicalLocations": [
                    {"name": "TST-0001", "kind": "module", "fullyQualifiedName": "TST-0001"},
                    {"name": "mount_slot", "kind": "member", "fullyQualifiedName": "TST-0001/mount_slot"}]}])

    def test_a_malformed_location_is_ignored_not_a_crash(self) -> None:
        for location in ("hw/x.yaml", {"line": 3}, {"file": ""}, {"file": 5}, []):
            with self.subTest(location=location):
                [result] = results_of([{"code": "x", "message": "m", "location": location}])
                self.assertNotIn("locations", result)

    def test_a_bad_line_falls_back_to_line_one(self) -> None:
        for line in (0, -3, "4", 1.5, True, None):
            with self.subTest(line=line):
                [result] = results_of([{"code": "x", "message": "m", "location": {"file": "hw/a.yaml", "line": line}}])
                self.assertEqual(result["locations"][0]["physicalLocation"]["region"], {"startLine": 1})

    def test_a_malformed_subject_is_ignored_not_a_crash(self) -> None:
        for subject in ("TST-0001", ["TST-0001"], {"PN": 5, "feature": ["f"]}, None):
            with self.subTest(subject=subject):
                [result] = results_of([{"code": "x", "message": "m", "subject": subject}])
                self.assertNotIn("locations", result)
                self.assertEqual(schema_errors(sarif.document([{"code": "x", "message": "m", "subject": subject}])), [])


class LocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hw = Path(self.tmp.name) / "hw"
        for pn, files in {"TST-0001": ["part.yaml", "model.py"], "TST-0002": ["model.py"], "TST-0003": []}.items():
            (self.hw / "parts" / pn).mkdir(parents=True)
            for name in files:
                (self.hw / "parts" / pn / name).write_text("x\n", encoding="utf-8")

    def anchor(self, finding: dict[str, Any]) -> dict[str, Any]:
        [result] = results_of([{"code": "x", "message": "m", **finding}], hw=self.hw)
        return result["locations"][0]["physicalLocation"]  # type: ignore[no-any-return]

    def test_a_part_anchors_on_line_one_of_its_part_yaml(self) -> None:
        self.assertEqual(self.anchor({"subject": {"PN": "TST-0001"}}),
                         {"artifactLocation": {"uri": "hw/parts/TST-0001/part.yaml"}, "region": {"startLine": 1}})

    def test_a_part_with_only_a_model_script_anchors_on_the_script(self) -> None:
        self.assertEqual(self.anchor({"subject": {"PN": "TST-0002"}})["artifactLocation"]["uri"],
                         "hw/parts/TST-0002/model.py")

    def test_the_legacy_part_keys_find_the_part_too(self) -> None:
        for key in ("part", "pn", "PN"):
            with self.subTest(key=key):
                self.assertEqual(self.anchor({key: "TST-0001"})["artifactLocation"]["uri"], "hw/parts/TST-0001/part.yaml")

    def test_a_part_with_neither_file_gets_no_physical_location(self) -> None:
        for pn in ("TST-0003", "TST-9999", "../escape"):
            with self.subTest(pn=pn):
                [result] = results_of([{"code": "x", "message": "m", "subject": {"PN": pn}}], hw=self.hw)
                self.assertNotIn("physicalLocation", result["locations"][0])

    def test_the_findings_own_file_and_line_win(self) -> None:
        found = self.anchor({"subject": {"PN": "TST-0001"}, "location": {"file": "hw/product.yaml", "line": 12}})
        self.assertEqual(found, {"artifactLocation": {"uri": "hw/product.yaml"}, "region": {"startLine": 12}})

    def test_the_findings_file_without_a_line_anchors_on_line_one(self) -> None:
        found = self.anchor({"location": {"file": "hw/product.yaml"}})
        self.assertEqual(found, {"artifactLocation": {"uri": "hw/product.yaml"}, "region": {"startLine": 1}})

    def test_a_windows_separator_in_the_file_becomes_a_slash(self) -> None:
        self.assertEqual(self.anchor({"location": {"file": "hw\\parts\\a.yaml"}})["artifactLocation"]["uri"],
                         "hw/parts/a.yaml")


class FingerprintTests(unittest.TestCase):
    def fingerprint(self, **subject: str) -> str:
        [result] = results_of([{"code": "fit_unverified", "message": "m", "subject": subject}])
        return result["partialFingerprints"]["cadFinding/v1"]  # type: ignore[no-any-return]

    def test_the_fingerprint_is_the_pinned_hash_of_code_pn_feature(self) -> None:
        self.assertEqual(self.fingerprint(PN="TST-0001", feature="pivot_bore"), "a28da1bffe6a75e8bb7e095adb300d52")

    def test_a_finding_with_no_part_or_feature_hashes_empty_fields(self) -> None:
        [result] = results_of([{"code": "min_wall", "message": "m"}])
        self.assertEqual(result["partialFingerprints"], {"cadFinding/v1": "85d773f435b240320841f7b914e81ccf"})

    def test_the_same_finding_in_two_runs_has_the_same_fingerprint(self) -> None:
        self.assertEqual(results_of([FULL]), results_of([dict(FULL)]))

    def test_message_remedy_and_line_do_not_change_it(self) -> None:
        other = FULL | {"message": "reworded", "remedy": "other", "location": {"file": "hw/x.yaml", "line": 99}}
        self.assertEqual(results_of([FULL])[0]["partialFingerprints"], results_of([other])[0]["partialFingerprints"])

    def test_a_different_code_part_or_feature_gives_a_different_fingerprint(self) -> None:
        base = self.fingerprint(PN="TST-0001", feature="a")
        self.assertNotEqual(base, self.fingerprint(PN="TST-0002", feature="a"))
        self.assertNotEqual(base, self.fingerprint(PN="TST-0001", feature="b"))
        self.assertNotEqual(base, self.fingerprint(PN="TST-0001"))
        [other] = results_of([{"code": "other_code", "message": "m", "subject": {"PN": "TST-0001", "feature": "a"}}])
        self.assertNotEqual(base, other["partialFingerprints"]["cadFinding/v1"])

    def test_a_part_and_a_feature_do_not_swap_into_the_same_hash(self) -> None:
        self.assertNotEqual(self.fingerprint(PN="TST-0001"), self.fingerprint(feature="TST-0001"))

    def test_the_legacy_part_and_feature_keys_give_the_subject_fingerprint(self) -> None:
        [legacy] = results_of([{"code": "fit_unverified", "message": "m", "part": "TST-0001", "feature": "pivot_bore"}])
        self.assertEqual(legacy["partialFingerprints"]["cadFinding/v1"], self.fingerprint(PN="TST-0001", feature="pivot_bore"))


class EmitTests(unittest.TestCase):
    RESULT = {"status": "fail", "findings": [FULL, {"check": "min_wall", "part": "TST-0001", "blocking": False}]}

    def printed(self, **kwargs: Any) -> str:
        stream = StringIO()
        with mock.patch.dict(os.environ, {emit.ENV: "json"}):
            emit.emit(self.RESULT, tool="t", name="t", code=1, stream=stream, **kwargs)
        return stream.getvalue()

    def test_the_default_output_is_the_unchanged_envelope(self) -> None:
        expected = json.dumps({"schema": "tool-result/1", "status": "fail", "findings": self.RESULT["findings"],
                               "payload": self.RESULT}, indent=2) + "\n"
        self.assertEqual(self.printed(), expected)
        self.assertEqual(self.printed(sarif=False), expected)

    def test_sarif_prints_the_exported_findings_instead_of_the_envelope(self) -> None:
        shown = json.loads(self.printed(sarif=True))
        self.assertEqual(schema_errors(shown), [])
        self.assertEqual(shown["version"], "2.1.0")
        self.assertEqual([r["ruleId"] for r in shown["runs"][0]["results"]], ["process.fdm.overhang", "min_wall"])
        self.assertEqual(shown["runs"][0]["properties"]["status"], "fail")
        self.assertEqual(shown["runs"][0]["tool"]["driver"]["name"], "t")

    def test_sarif_is_full_json_even_when_the_mode_is_compact(self) -> None:
        stream = StringIO()
        with mock.patch.dict(os.environ, {emit.ENV: "compact"}):
            emit.emit(self.RESULT, tool="t", name="t", code=1, stream=stream, sarif=True)
        self.assertEqual(json.loads(stream.getvalue())["version"], "2.1.0")

    def test_sarif_leaves_the_out_file_as_the_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            self.printed(sarif=True, out=out)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["schema"], "tool-result/1")

    def test_a_blocking_failures_report_exports_those_failures(self) -> None:
        shown = json.loads(emit.sarif_text({"status": "fail", "blocking_failures": ["rule-1: too steep"]}))
        self.assertEqual(shown["runs"][0]["results"][0]["message"]["text"], "rule-1: too steep")

    def test_a_result_that_is_not_a_mapping_exports_no_results(self) -> None:
        shown = json.loads(emit.sarif_text(["not", "a", "report"]))
        self.assertEqual(shown["runs"][0]["results"], [])

    def test_the_command_line_converts_a_stored_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stored = Path(tmp) / "envelope.json"
            stored.write_text(json.dumps(emit.envelope(self.RESULT)), encoding="utf-8")
            stream = StringIO()
            with contextlib.redirect_stdout(stream):
                code = emit.main(["--sarif", str(stored)])
            self.assertEqual(code, 0)
            self.assertEqual(schema_errors(json.loads(stream.getvalue())), [])

    def test_the_command_line_needs_the_sarif_flag_and_a_readable_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            for argv in ([str(bad)], ["--sarif", str(bad)], ["--sarif", str(Path(tmp) / "missing.json")]):
                with self.subTest(argv=argv), contextlib.redirect_stderr(StringIO()):
                    self.assertEqual(emit.main(argv), 2)


if __name__ == "__main__":
    unittest.main()
