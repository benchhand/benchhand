#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""assembly_operations: one normalizer of operations, its drawing.operations refusal, declared
insertion paths, and the pure joint-evidence checks with their `assembly.joint` outcomes. Kernel-free; the
order checks of plan section 11 row 15 are in skills/cad-motion/tests/test_access.py beside the geometric ones."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module as load  # noqa: E402


ops = load("cad_record_assembly_operations", SCRIPTS / "assembly_operations.py")
assembly_data = load("cad_record_assembly_data", SCRIPTS / "assembly_data.py")
fx = load("cad_record_mi_fixtures", HERE / "fixtures" / "manufacturing_intent.py")
intent = fx.intent_module()
evidence = load("cad_record_evidence", SCRIPTS / "evidence.py")
check_packet = load("cad_record_check_packet", SCRIPTS / "check_packet.py")

PRESS = {"id": "fit-bearing", "kind": "press_fit", "part": "TST-0003", "into": "TST-0001", "interface": "IF-001",
         "direction": [0, 0, -2], "acceptance": {"kind": "seated", "features": ["bearing_face", "seat_floor"]},
         "tools": ["arbor press"]}


def validator(definition: str):
    return fx.ref_validator(f"https://srobroek.com/cad/schemas/manufacturing.schema.json#/$defs/{definition}")


class NormalizeTests(unittest.TestCase):
    def test_operations_normalize_from_the_top_level_list(self) -> None:
        current = ops.normalize({"operations": [PRESS]})
        self.assertEqual(current.authority, "operations")
        [op] = current.items
        self.assertEqual(op.direction, (0.0, 0.0, -1.0))
        self.assertTrue(op.installs and op.geometric_acceptance)
        self.assertEqual(op.tool_names, ("arbor press",))
        self.assertFalse(op.tools[0].declared or op.tools[0].reach_known)
        self.assertIsNone(op.tools[0].envelope_artifact)

    def test_drawing_operations_are_refused_beside_or_instead_of_the_top_level_list(self) -> None:
        for assembly in ({"operations": [PRESS], "drawing": {"operations": [PRESS]}},
                         {"drawing": {"number": "D-1", "operations": [PRESS]}}):
            with self.subTest(assembly=assembly), self.assertRaises(ops.OperationsError) as caught:
                ops.normalize(assembly)
            self.assertEqual(caught.exception.code, "intent.dual_authority")

    def test_unknown_or_absent_operations_stay_unknown(self) -> None:
        unknown = ops.normalize({"operations": fx.unknown("not planned yet")})
        self.assertEqual((unknown.items, unknown.unknown), ((), "not planned yet"))
        absent = ops.normalize({"subassemblies": []})
        self.assertEqual((absent.authority, absent.unknown), ("none", "assembly.yaml records no operations"))

    def test_malformed_operations_are_refused(self) -> None:
        def changed(**fields):
            return {**copy.deepcopy(PRESS), **fields}

        cases = {
            "unknown kind": [changed(kind="glue")],
            "press fit without interface": [{k: v for k, v in PRESS.items() if k != "interface"}],
            "close without closes": [{"id": "shut", "kind": "close", "part": "TST-0002", "into": "TST-0001"}],
            "zero direction": [changed(direction=[0, 0, 0])],
            "seated with one feature": [changed(acceptance={"kind": "seated", "features": ["a"]})],
            "repeated id": [PRESS, PRESS],
            "tool without name": [changed(tool_access=[{"approach": [0, 0, -1]}])],
        }
        for name, items in cases.items():
            with self.subTest(name), self.assertRaises(ops.OperationsError):
                ops.normalize({"operations": items})

    def test_a_tool_envelope_names_its_solid_only_when_its_fact_cites_one(self) -> None:
        envelope = fx.known("TST socket", "supplier_typical", sources=[fx.source(
            "supplier", "TST socket CAD", artifact="manufacturing/sources/socket.step", sha256="a" * 64)])
        item = {**PRESS, "tool_access": [{"tool": "socket", "approach": [0, 0, -1], "envelope": envelope,
                                          "reach": fx.known(fx.quantity(80), "supplier_typical", sources=[fx.source("supplier")])},
                                         {"tool": "wrench", "envelope": fx.known("open-end wrench")}]}
        [op] = ops.normalize({"operations": [item]}).items
        socket, wrench = op.tools[1:]
        self.assertEqual(socket.envelope_artifact, ("manufacturing/sources/socket.step", "a" * 64))
        self.assertTrue(socket.reach_known)
        self.assertIsNone(wrench.envelope_artifact)

    def test_a_normalized_record_reads_the_same_operations(self) -> None:
        documents = fx.tst_record()
        with tempfile.TemporaryDirectory() as directory:
            hw = fx.write_record(Path(directory) / "hw", documents)
            record = intent.load_record(hw)
        from_record = ops.from_record(record)
        direct = ops.normalize(documents["assembly.yaml"])
        self.assertEqual(ops.summary(from_record), ops.summary(direct))
        self.assertEqual(from_record.ids, ("fit_bearing",))

    def test_a_declared_insertion_path_arrives_along_the_direction(self) -> None:
        item = {**PRESS, "path": {"start": [10, 0, 30], "via": [[0, 0, 30]]}}
        [op] = ops.normalize({"operations": [item]}).items
        self.assertEqual(op.path, ((10.0, 0.0, 30.0), (0.0, 0.0, 30.0)))
        self.assertEqual(ops.summary(ops.normalize({"operations": [item]}))[0]["path"], [[10.0, 0.0, 30.0], [0.0, 0.0, 30.0]])
        [request] = ops.access_plan(ops.normalize({"operations": [item]}))
        self.assertEqual(request.path, op.path)
        [default] = ops.access_plan(ops.normalize({"operations": [PRESS]}))
        self.assertIsNone(default.path, "without a path the straight line along direction is the default")
        refused = {
            "last leg across the direction": {**PRESS, "path": {"start": [10, 0, 30]}},
            "last leg against the direction": {**PRESS, "path": {"start": [0, 0, -5]}},
            "a leg of zero length": {**PRESS, "path": {"start": [0, 0, 30], "via": [[0, 0, 30]]}},
            "a path on an inspection": {"id": "look", "kind": "inspect", "part": "TST-0003", "direction": [0, 0, -1],
                                        "acceptance": {"kind": "inspected", "features": ["bore"]}, "path": {"start": [0, 0, 5]}},
            "edge distance on a closure": {"id": "shut", "kind": "close", "part": "TST-0002", "into": "TST-0001",
                                           "closes": ["TST-0003"], "edge_distance": {}},
        }
        for name, bad in refused.items():
            with self.subTest(name), self.assertRaises(ops.OperationsError):
                ops.normalize({"operations": [bad]})


class JointTests(unittest.TestCase):
    """Joint evidence, one assembly.joint outcome per joining operation: retention, thread engagement and
    torque, edge distance against its criterion, and the host's temperature limit."""

    def record(self, *, temperature=None, limit: float | None = 60.0, retention=None, sourced: bool = True,
               limit_source: dict | None = None) -> tuple:
        """The TST record and its operations; the host's limit is a datasheet value (or `limit_source`) unless not `sourced` (an estimate)."""
        documents = fx.tst_record()
        operation = documents["assembly.yaml"]["operations"][0]
        if retention is not None:
            operation["retention"] = retention
        if temperature is not None:
            documents["product.yaml"]["use_case"]["environment"]["temperature"] = temperature
        if limit is not None:
            profile = next(p for p in documents["manufacturing/materials.yaml"]["profiles"]
                           if p["id"] == "tst_polymer_printed")
            datasheet = {"basis": "supplier_typical", "sources": [limit_source or fx.source("datasheet", "TST polymer datasheet")]}
            profile["properties"].append(fx.material_property("max_service_temperature", limit, "deg_C",
                                                              orientation=fx.known("XY"), **(datasheet if sourced else {})))
        with tempfile.TemporaryDirectory() as directory:
            record = intent.load_record(fx.write_record(Path(directory) / "hw", documents))
        return record, ops.from_record(record)

    def codes(self, report) -> dict[str, str]:
        """Each finding's code and outcome; every finding and outcome is schema-valid and names its obligation."""
        finding, outcome = validator("finding"), validator("obligation_outcome")
        for item in report.findings:
            self.assertEqual(list(finding.iter_errors(item)), [], item["id"])
            self.assertEqual(item["obligation"], f"assembly.joint@op:{item['subject']['assembly_operation']}")
        for row in report.outcomes:
            self.assertEqual(list(outcome.iter_errors(row)), [], row["obligation"])
        return {item["code"]: item["outcome"] for item in report.findings}

    def fasten(self, **fields) -> object:
        sourced = {"sources": [fx.source("supplier", "TST insert datasheet")], "basis": "supplier_typical"}
        item = {"id": "screw", "kind": "fasten", "part": "TST-0002", "into": "TST-0001", "direction": [0, 0, -1],
                "acceptance": {"kind": "torqued", "features": ["head"]},
                "retention": fx.known("TST insert pull-out 400 N", **sourced),
                "engagement": fx.known(fx.quantity(6), **sourced), "torque": fx.known(fx.quantity(0.8, "N_m"), **sourced)}
        return ops.normalize({"operations": [{**item, **fields}]})

    def test_a_press_fit_without_retention_evidence_stays_unknown(self) -> None:
        record, operations = self.record(temperature=fx.known(fx.quantity(lower=10, upper=40, units="deg_C")))
        report = ops.check_joints(operations, record, intent.resolve_property)
        self.assertEqual(self.codes(report), {"assembly.retention_unknown": "unknown"})
        row = report.outcome("assembly.joint@op:fit_bearing")
        self.assertEqual((row["outcome"], row["level"], row["findings"]), ("unknown", "process_planned", ["fit_bearing_retention_unknown"]))
        self.assertIn("retention", row["uncertainty"])
        self.assertEqual(ops.JOINING, intent.JOINING_KINDS, "the record's joint obligations and this check cover one set")

    def test_a_host_softening_in_service_fails_the_joint(self) -> None:
        retention = fx.known("TST pull-out test at 80 deg_C", "measured", sources=[fx.source("test", "TST pull-out")])
        record, operations = self.record(temperature=fx.known(fx.quantity(lower=10, upper=80, units="deg_C")),
                                         retention=retention)
        report = ops.check_joints(operations, record, intent.resolve_property)
        self.assertEqual(self.codes(report), {"assembly.joint_temperature": "fail"})
        self.assertEqual(report.findings[0]["severity"], "critical")
        self.assertEqual(report.findings[0]["criterion"]["value"], 60.0)
        self.assertEqual(report.outcome("assembly.joint@op:fit_bearing")["outcome"], "fail")

    def test_an_unknown_service_temperature_or_limit_stays_unknown(self) -> None:
        record, operations = self.record(limit=None)
        found = self.codes(ops.check_joints(operations, record, intent.resolve_property))
        self.assertEqual(found, {"assembly.retention_unknown": "unknown", "assembly.joint_temperature_unknown": "unknown"})

    def test_a_host_that_resolves_to_no_part_keeps_the_joint_unknown(self) -> None:
        """Sourced retention alone never passes a press fit whose host material cannot be read: an occurrence
        reference with no part_of resolution, or a host without a material profile, stays unknown."""
        retention = fx.known("TST pull-out test", "measured", sources=[fx.source("test", "TST pull-out")])
        record, _ = self.record(temperature=fx.known(fx.quantity(lower=10, upper=40, units="deg_C")), retention=retention)
        press = {"id": "fit_bearing", "kind": "press_fit", "part": "TST-0003", "into": "@frame", "interface": "IF-001",
                 "direction": [0, 0, -1], "acceptance": {"kind": "seated", "features": ["bearing_face", "seat_floor"]},
                 "retention": retention}
        report = ops.check_joints(ops.normalize({"operations": [press]}), record, intent.resolve_property)
        self.assertEqual(self.codes(report), {"assembly.joint_temperature_unknown": "unknown"})
        self.assertIn("@frame resolves to no part", report.findings[0]["measured"]["reason"])
        resolved = ops.check_joints(ops.normalize({"operations": [press]}), record, intent.resolve_property,
                                    part_of=lambda ref: "TST-0001" if ref == "@frame" else ref)
        self.assertEqual(resolved.outcome("assembly.joint@op:fit_bearing")["outcome"], "pass")

    def test_an_estimated_host_limit_can_fail_a_joint_but_never_pass_it(self) -> None:
        """A limit that is an estimate cannot show the host holds: within it the joint stays unknown even with
        sourced retention; above it the joint still fails."""
        retention = fx.known("TST pull-out test", "measured", sources=[fx.source("test", "TST pull-out")])
        record, operations = self.record(temperature=fx.known(fx.quantity(lower=10, upper=40, units="deg_C")),
                                         retention=retention, sourced=False)
        within = ops.check_joints(operations, record, intent.resolve_property)
        self.assertEqual(self.codes(within), {"assembly.joint_temperature_unknown": "unknown"})
        self.assertIn("max_service_temperature 60 deg_C (estimate) is not a qualified limit",
                      within.findings[0]["measured"]["reason"])
        self.assertEqual(within.outcome("assembly.joint@op:fit_bearing")["outcome"], "unknown")
        record, operations = self.record(temperature=fx.known(fx.quantity(lower=10, upper=80, units="deg_C")),
                                         retention=retention, sourced=False)
        above = ops.check_joints(operations, record, intent.resolve_property)
        self.assertEqual(self.codes(above), {"assembly.joint_temperature": "fail"})
        self.assertEqual(above.outcome("assembly.joint@op:fit_bearing")["outcome"], "fail")

    def test_a_guideline_sourced_host_limit_never_passes_a_joint(self) -> None:
        retention = fx.known("TST pull-out test", "measured", sources=[fx.source("test", "TST pull-out")])
        record, operations = self.record(temperature=fx.known(fx.quantity(lower=10, upper=40, units="deg_C")), retention=retention,
                                         limit_source=fx.source("guideline", "fdm.generic", revision="1", sha256="a" * 64))
        within = ops.check_joints(operations, record, intent.resolve_property)
        self.assertEqual(self.codes(within), {"assembly.joint_temperature_unknown": "unknown"})
        self.assertEqual(within.outcome("assembly.joint@op:fit_bearing")["outcome"], "unknown")

    def test_a_fastener_needs_sourced_engagement_torque_and_edge_distance(self) -> None:
        record, _ = self.record(limit=None)
        fasten = ops.normalize({"operations": [{"id": "screw", "kind": "fasten", "part": "TST-0002", "into": "TST-0001",
                                                "direction": [0, 0, -1],
                                                "acceptance": {"kind": "torqued", "features": ["head"]},
                                                "torque": fx.unknown("not specified")}]})
        found = self.codes(ops.check_joints(fasten, record, intent.resolve_property))
        self.assertEqual(found, {"assembly.retention_unknown": "unknown", "assembly.engagement_unknown": "unknown",
                                 "assembly.torque_unknown": "unknown", "assembly.edge_distance_unknown": "unknown"})

    def test_the_edge_distance_is_judged_against_its_criterion(self) -> None:
        record, _ = self.record(limit=None)
        criterion = fx.criterion("edge_distance", ">=", 4.5, "mm", source=fx.source("standard", "TST-STD-2 insert edge distance"))
        cases = {
            "meets its minimum": (fx.known(fx.quantity(lower=4.5, upper=5.0)), "pass", {}),
            "short of its minimum": (fx.known(fx.quantity(4200, "um")), "fail", {"assembly.edge_distance": "fail"}),
            "not measured": (fx.unknown("not measured on the model"), "unknown", {"assembly.edge_distance_unknown": "unknown"}),
        }
        for name, (distance, outcome, codes) in cases.items():
            with self.subTest(name):
                report = ops.check_joints(self.fasten(edge_distance={"criterion": criterion, "distance": distance}),
                                          record, intent.resolve_property)
                self.assertEqual(self.codes(report), codes)
                self.assertEqual(report.outcome("assembly.joint@op:screw")["outcome"], outcome)
        [failed] = ops.check_joints(self.fasten(edge_distance={"criterion": criterion, "distance": fx.known(fx.quantity(4))}),
                                    record, intent.resolve_property).findings
        self.assertEqual((failed["severity"], failed["criterion"]["source"]["reference"]), ("critical", "TST-STD-2 insert edge distance"))
        between = fx.criterion("edge_distance", "between", [3, 6], "mm")
        wide = ops.check_joints(self.fasten(edge_distance={"criterion": between, "distance": fx.known(fx.quantity(lower=5, upper=7))}),
                                record, intent.resolve_property)
        self.assertEqual(self.codes(wide), {"assembly.edge_distance": "fail"}, "a between criterion bounds the upper end too")

    def test_a_strict_edge_distance_criterion_is_refused_never_read_as_a_minimum(self) -> None:
        record, _ = self.record(limit=None)
        for operator in (">", "<", "<="):
            with self.subTest(operator=operator):
                criterion = fx.criterion("edge_distance", operator, 4.5, "mm")
                report = ops.check_joints(self.fasten(edge_distance={"criterion": criterion, "distance": fx.known(fx.quantity(9))}),
                                          record, intent.resolve_property)
                self.assertEqual(self.codes(report), {"assembly.edge_distance_unknown": "unknown"})
                self.assertEqual(report.outcome("assembly.joint@op:screw")["outcome"], "unknown")


class NeverInstalledTests(unittest.TestCase):
    """An occurrence no operation installs, other than the one base, is a missing installing operation."""

    PNS = ("TST-0101", "TST-0102", "TST-0103")
    SEAT = {"id": "seat-bearing", "kind": "press_fit", "part": "TST-0102", "into": "TST-0101", "interface": "IF-001",
            "direction": [0, 0, -1], "acceptance": {"kind": "seated", "features": ["outer_face", "seat_floor"]}}
    COVER = {"id": "fit-cover", "kind": "join", "part": "TST-0103", "into": "TST-0101", "direction": [0, 0, -1],
             "acceptance": {"kind": "standoff", "features": ["cover_foot", "housing_top"]}}

    def check(self, items: list[dict], pns: tuple[str, ...] = PNS, with_inventory: bool = True):
        inventory = assembly_data.normalize({"subassemblies": [{"id": "main", "title": "Main", "parts": list(pns)}]})
        return ops.check_order(ops.normalize({"operations": items}), inventory if with_inventory else None)

    def test_an_occurrence_no_operation_names_fails_the_product_order(self) -> None:
        report = self.check([self.SEAT])
        [finding] = report.findings
        self.assertEqual((finding["code"], finding["subject"], finding["severity"]),
                         ("assembly.never_installed", {"PN": "TST-0103"}, "critical"))
        self.assertIn("TST-0103", finding["measured"]["text"])
        self.assertEqual(finding["criterion"]["source"]["origin"], "benchhand")
        row = report.outcome("assembly.order@product")
        self.assertEqual((row["outcome"], row["findings"]), ("fail", [finding["id"]]))
        self.assertEqual(report.outcome("assembly.order@op:seat-bearing")["outcome"], "pass")
        for item in report.findings:
            self.assertEqual(list(validator("finding").iter_errors(item)), [], item["id"])
        for outcome in report.outcomes:
            self.assertEqual(list(validator("obligation_outcome").iter_errors(outcome)), [], outcome["obligation"])

    def test_every_occurrence_installed_or_receiving_passes(self) -> None:
        report = self.check([self.SEAT, self.COVER])
        self.assertEqual(report.findings, ())
        self.assertEqual({row["obligation"] for row in report.outcomes},
                         {"assembly.order@op:seat-bearing", "assembly.order@op:fit-cover", "assembly.order@product"})
        self.assertEqual(report.outcome("assembly.order@product")["outcome"], "pass")

    def test_each_missing_installer_is_named(self) -> None:
        report = self.check([self.SEAT], pns=(*self.PNS, "TST-0104"))
        self.assertEqual(sorted(f["subject"]["PN"] for f in report.findings), ["TST-0103", "TST-0104"])

    def test_a_receiver_nobody_installs_is_a_second_base(self) -> None:
        # The screw fastens into the cover. The bearing presses into the housing. Nothing installs either
        # receiver. The sequence names the housing first, so the housing is the base and the cover floats.
        screw = {**self.SEAT, "id": "fasten-cover", "kind": "fasten", "part": "TST-0104", "into": "TST-0103"}
        report = self.check([self.SEAT, screw], pns=(*self.PNS, "TST-0104"))
        [finding] = report.findings
        self.assertEqual((finding["code"], finding["subject"]), ("assembly.never_installed", {"PN": "TST-0103"}))
        self.assertIn("TST-0101", finding["measured"]["text"], "the finding names the base it floats beside")
        self.assertEqual(report.outcome("assembly.order@product")["outcome"], "fail")

    def test_a_part_only_closed_over_is_a_second_base(self) -> None:
        close = {"id": "fit-cover", "kind": "close", "part": "TST-0103", "into": "TST-0101", "closes": ["TST-0102"],
                 "direction": [0, 0, -1]}
        report = self.check([close])
        self.assertEqual([f["subject"]["PN"] for f in report.findings], ["TST-0102"])

    def test_occurrences_whose_ids_differ_only_by_dash_or_underscore_get_distinct_finding_ids(self) -> None:
        record = {"occurrences": [{"id": name, "PN": pn} for name, pn in
                                  (("base", "TST-0101"), ("clip-a", "TST-0102"), ("clip_a", "TST-0102"),
                                   ("clip-b", "TST-0102"))],
                  "subassemblies": [{"id": "main", "title": "Main", "occurrences": ["@base", "@clip-a", "@clip_a", "@clip-b"]}]}
        join = {**self.COVER, "id": "mount-clip", "part": "@clip-b", "into": "@base"}
        report = ops.check_order(ops.normalize({"operations": [join]}),
                                assembly_data.normalize(record, {"TST-0102": "part_local"}))
        ids = [f["id"] for f in report.findings]
        self.assertEqual(len(report.findings), 2, ids)
        self.assertEqual(len(set(ids)), 2, ids)
        self.assertEqual(report.outcome("assembly.order@product")["findings"], ids)

    def test_one_base_plus_installed_parts_is_clean(self) -> None:
        chain = {**self.SEAT, "id": "mount-cover", "part": "TST-0103", "into": "TST-0102"}
        self.assertEqual(self.check([self.SEAT, chain]).findings, ())

    def test_an_empty_known_sequence_over_two_parts_leaves_one_floating(self) -> None:
        report = self.check([], pns=("TST-0101", "TST-0102"))
        [finding] = report.findings
        self.assertEqual(finding["code"], "assembly.never_installed")
        self.assertEqual(report.outcome("assembly.order@product")["outcome"], "fail")

    def test_an_empty_known_sequence_over_one_part_is_its_base(self) -> None:
        report = self.check([], pns=("TST-0101",))
        self.assertEqual(report.findings, ())
        self.assertEqual(report.outcome("assembly.order@product")["outcome"], "pass")

    def test_without_an_inventory_there_is_no_occurrence_to_miss(self) -> None:
        self.assertEqual(self.check([self.SEAT], with_inventory=False).findings, ())

    def test_existing_receivers_are_present_from_the_start(self) -> None:
        # The bearing presses into one piece of existing equipment and the screw fastens into another.
        screw = {**self.SEAT, "id": "fasten-cover", "kind": "fasten", "part": "TST-0104", "into": "TST-0103"}
        pns = (*self.PNS, "TST-0104")
        inventory = assembly_data.normalize({"subassemblies": [{"id": "main", "title": "Main", "parts": list(pns)}]})
        report = ops.check_order(ops.normalize({"operations": [self.SEAT, screw]}), inventory,
                                 existing=frozenset({"TST-0101", "TST-0103"}))
        self.assertEqual(report.findings, ())
        self.assertEqual(report.outcome("assembly.order@product")["outcome"], "pass")

    def test_existing_equipment_does_not_take_the_products_one_base(self) -> None:
        # TST-0101 is existing equipment. Of the product parts nobody installs, TST-0103 is the base and TST-0104 floats.
        pns = (*self.PNS, "TST-0104")
        inventory = assembly_data.normalize({"subassemblies": [{"id": "main", "title": "Main", "parts": list(pns)}]})
        report = ops.check_order(ops.normalize({"operations": [self.SEAT]}), inventory, existing=frozenset({"TST-0101"}))
        self.assertEqual([f["subject"]["PN"] for f in report.findings], ["TST-0104"])


class NeverInstalledPacketTests(unittest.TestCase):
    """A missing installer reaches the user through the packet and the evidence assessment, not only check_order."""

    PNS = ("TST-0101", "TST-0102", "TST-0103")
    SEAT = NeverInstalledTests.SEAT

    def assessed(self, items: list[dict]):
        documents = fx.record(parts=[fx.part(pn, pn, process="cnc") for pn in self.PNS], operations=items,
                              interfaces=[fx.interface("IF-001", "TST-0101", "TST-0102", "mounting")])
        record = intent.normalize_record({rel: doc for rel, doc in documents.items() if not rel.startswith("_")})
        inventory = assembly_data.normalize(documents["assembly.yaml"])
        report = ops.check_order(ops.normalize({"operations": items}), inventory)
        expected = [o for o in intent.required_checks(record) if o.family == "assembly.order"]
        data = check_packet.build(record, expected, "test producer", list(report.outcomes), list(report.findings), {})
        packet = evidence.ProducerPacket("hw/checks/v1/assembly-access", "b" * 64, data)
        result = evidence.assess(record, version="v1", manifest_sha256="a" * 64, packets=[packet])
        return expected, result

    def test_the_product_order_obligation_is_required_and_fails_a_missing_installer(self) -> None:
        expected, result = self.assessed([self.SEAT])
        self.assertIn("assembly.order@product", {o.id for o in expected})
        self.assertEqual(result.statuses["assembly.order@product"].result, "fail")
        self.assertNotIn("packet.invalid", {f.code for f in result.findings})

    def test_removing_the_sole_installer_of_a_two_part_assembly_fails_the_product_order(self) -> None:
        self.PNS = ("TST-0101", "TST-0102")  # the operation is the sole installer, so removing it empties the list
        full = self.assessed([self.SEAT])[1]
        self.assertEqual(full.statuses["assembly.order@product"].result, "pass")
        _, result = self.assessed([])
        self.assertEqual(result.statuses["assembly.order@product"].result, "fail")

    def test_a_complete_sequence_passes_the_product_order_obligation(self) -> None:
        cover = NeverInstalledTests.COVER
        _, result = self.assessed([self.SEAT, cover])
        self.assertEqual(result.statuses["assembly.order@product"].result, "pass")


DATASHEET = [{"kind": "datasheet", "reference": "Loctite EA 9466 TDS, cure schedule", "url": "https://example.com/ea9466.pdf",
              "accessed": "2026-11-24"}]


def sourced(value: float, units: str, sources: list | None = None) -> dict:
    return {"status": "known", "value": {"value": value, "units": units}, "basis": "supplier_typical",
            "sources": DATASHEET if sources is None else sources}


class SubBuildTests(unittest.TestCase):
    """A bench sub-build has its own ground, sequence and state; one install operation puts its output in place."""

    PNS = ("TST-0101", "TST-0102")  # housing, hanger
    MAGNET = "TST-0103"

    def inventory(self, extra: tuple[str, ...] = ()):
        record = {"occurrences": [{"id": name, "PN": self.MAGNET} for name in ("mag-a", "mag-b")],
                  "subassemblies": [{"id": "main", "title": "Main", "parts": [self.PNS[0], *extra]},
                                    {"id": "hanger", "title": "Hanger", "parts": [self.PNS[1]],
                                     "occurrences": ["@mag-a", "@mag-b"]}]}
        return assembly_data.normalize(record, {self.MAGNET: "part_local"})

    @staticmethod
    def bond(magnet: str) -> dict:
        return {"id": f"bond-{magnet}", "kind": "join", "sub_build": "hanger", "part": f"@{magnet}", "into": "TST-0102",
                "direction": [0, 0, -1], "acceptance": {"kind": "flush", "features": ["magnet_face", "pocket_rim"]},
                "consumables": ["epoxy"]}

    CURE = {"id": "cure-hanger", "kind": "hold", "sub_build": "hanger", "part": "TST-0102",
            "duration": sourced(24, "h"), "fixture": "bench cure jig", "orientation": [0, 0, 1],
            "cure": {"time": sourced(24, "h"), "temperature": sourced(23, "deg_C")}}
    INSTALL = {"id": "install-hanger", "kind": "install", "part": "hanger", "into": "TST-0101", "direction": [0, 0, -1],
               "acceptance": {"kind": "engaged", "features": ["hinge_pin"]}}

    def items(self) -> list[dict]:
        return [self.bond("mag-a"), self.bond("mag-b"), dict(self.CURE), dict(self.INSTALL)]

    def test_magnets_bonded_on_the_bench_then_the_hanger_installed_is_clean(self) -> None:
        operations = ops.normalize({"operations": self.items()})
        state = ops.sequence(operations, self.inventory())
        self.assertEqual(state.ground, "TST-0101")
        self.assertEqual(state.floating, frozenset())
        hanger = state.sub_builds["hanger"]
        self.assertEqual((hanger.ground, hanger.floating), ("TST-0102", frozenset()))
        report = ops.check_order(operations, self.inventory())
        self.assertEqual(report.findings, ())
        self.assertEqual({row["outcome"] for row in report.outcomes}, {"pass"})
        self.assertEqual(report.outcome("assembly.order@op:cure-hanger")["outcome"], "pass")

    def test_the_bench_record_is_valid_against_the_schema(self) -> None:
        check = fx.ref_validator("https://srobroek.com/cad/schemas/assembly.schema.json#/$defs/assembly_operation")
        for item in self.items():
            self.assertEqual(list(check.iter_errors(item)), [], item["id"])
        self.assertNotEqual(list(check.iter_errors({**self.INSTALL, "part": "TST-0102"})), [], "an install names a sub-build")
        self.assertNotEqual(list(check.iter_errors({**self.bond("mag-a"), "duration": sourced(1, "h")})), [],
                            "only a hold has a duration")

    def test_the_cure_hold_sees_only_its_sub_build(self) -> None:
        clamped = {**self.CURE, "tools": ["spring clamp"]}
        items = [self.bond("mag-a"), self.bond("mag-b"), clamped, dict(self.INSTALL)]
        plan = {request.operation.id: request
                for request in ops.access_plan(ops.normalize({"operations": items}), self.inventory())}
        cure = plan["cure-hanger"]
        self.assertEqual(cure.tool_obstacles, frozenset({"mag-a", "mag-b"}), "the housing is not there during the cure")
        install = plan["install-hanger"]
        self.assertEqual((install.moving, install.frame, install.unit), ("TST-0102", "TST-0101",
                                                                         frozenset({"TST-0102", "mag-a", "mag-b"})))
        self.assertEqual(install.path_obstacles, frozenset(), "the magnets travel with the hanger")

    def test_installing_a_sub_build_before_its_own_steps_finish_is_an_order_error(self) -> None:
        bond_a, bond_b, cure, install = self.items()
        report = ops.check_order(ops.normalize({"operations": [bond_a, install, bond_b, cure]}), self.inventory())
        [finding] = [f for f in report.findings if f["code"] == "assembly.sub_build_unfinished"]
        self.assertEqual(finding["obligation"], "assembly.order@op:install-hanger")
        self.assertIn("bond-mag-b, cure-hanger", finding["measured"]["text"])
        self.assertEqual(report.outcome("assembly.order@op:install-hanger")["outcome"], "fail")

    def test_a_part_used_in_two_sub_builds_is_an_error(self) -> None:
        tab = {**self.bond("mag-b"), "id": "bond-tab", "sub_build": "tab", "into": "TST-0104"}
        install_tab = {**self.INSTALL, "id": "install-tab", "part": "tab"}
        report = ops.check_order(ops.normalize({"operations": [*self.items(), tab, install_tab]}),
                                 self.inventory(extra=("TST-0104",)))
        [finding] = [f for f in report.findings if f["code"] == "assembly.sub_build_shared"]
        self.assertEqual(finding["obligation"], "assembly.order@op:bond-tab")
        self.assertIn("@mag-b", finding["measured"]["text"])
        self.assertIn("sub-build hanger", finding["measured"]["text"])

    def test_install_names_a_sub_build_that_exists_and_every_sub_build_is_installed(self) -> None:
        bond_a, bond_b, cure, install = self.items()
        for name, items, code, op_id in (
                ("unknown unit", [bond_a, bond_b, cure, install, {**install, "id": "install-lid", "part": "lid"}],
                 "assembly.sub_build_unknown", "install-lid"),
                ("never installed", [bond_a, bond_b, cure], "assembly.sub_build_not_installed", "cure-hanger"),
                ("installed twice", [bond_a, bond_b, cure, install, {**install, "id": "again"}],
                 "assembly.installed_twice", "again")):
            with self.subTest(name):
                report = ops.check_order(ops.normalize({"operations": items}), self.inventory())
                self.assertIn(code, {f["code"] for f in report.findings})
                self.assertEqual(report.outcome(f"assembly.order@op:{op_id}")["outcome"], "fail")

    def test_a_main_closure_before_the_unit_install_fails_the_install_of_each_enclosed_member(self) -> None:
        for enclosed in ("@mag-a", "TST-0102"):
            with self.subTest(enclosed):
                lid = {"id": "close-lid", "kind": "close", "part": "TST-0104", "into": "TST-0101", "closes": [enclosed]}
                report = ops.check_order(ops.normalize({"operations": [lid, *self.items()]}),
                                         self.inventory(extra=("TST-0104",)))
                [finding] = report.findings
                self.assertEqual((finding["code"], finding["obligation"]),
                                 ("assembly.closed_before_install", "assembly.order@op:install-hanger"))
                self.assertIn(f"{enclosed.lstrip('@')}, which close-lid encloses", finding["measured"]["text"])
                self.assertEqual(report.outcome("assembly.order@op:close-lid")["outcome"], "pass")

    def test_an_occurrence_named_like_a_sub_build_is_not_its_install(self) -> None:
        record = {"occurrences": [*({"id": name, "PN": self.MAGNET} for name in ("mag-a", "mag-b")),
                                  {"id": "hanger", "PN": "TST-0105"}],
                  "subassemblies": [{"id": "main", "title": "Main", "parts": [self.PNS[0]], "occurrences": ["@hanger"]},
                                    {"id": "hanger", "title": "Hanger", "parts": [self.PNS[1]],
                                     "occurrences": ["@mag-a", "@mag-b"]}]}
        inventory = assembly_data.normalize(record, {self.MAGNET: "part_local", "TST-0105": "part_local"})
        pin = {"id": "insert-pin", "kind": "insert", "part": "@hanger", "into": "TST-0101", "direction": [0, 0, -1],
               "acceptance": {"kind": "engaged", "features": ["pin_bore"]}}
        bond_a, bond_b, cure, install = self.items()
        missing = ops.check_order(ops.normalize({"operations": [bond_a, bond_b, cure, pin]}), inventory)
        self.assertEqual([f["code"] for f in missing.findings], ["assembly.sub_build_not_installed"])
        complete = ops.check_order(ops.normalize({"operations": [bond_a, bond_b, cure, pin, install]}), inventory)
        self.assertEqual(complete.findings, ())

    def test_a_mistyped_bench_receiver_leaves_its_step_unknown(self) -> None:
        bond_a, bond_b, cure, install = self.items()
        typo = {**bond_a, "into": "TST-9999"}
        inventory = self.inventory()
        state = ops.sequence(ops.normalize({"operations": [typo, bond_b, cure, install]}), inventory)
        self.assertEqual(state.sub_builds["hanger"].ground, "TST-0102")
        self.assertNotIn("TST-9999", state.sub_builds["hanger"].members)
        report = ops.check_order(ops.normalize({"operations": [typo, bond_b, cure, install]}), inventory)
        self.assertEqual(report.outcome("assembly.order@op:bond-mag-a")["outcome"], "unknown")
        self.assertEqual(report.findings, ())

    def test_a_tool_free_hold_reaches_nothing_and_a_tool_on_it_is_planned(self) -> None:
        hold = {"id": "cure", "kind": "hold", "part": "TST-0001", "duration": fx.known(fx.quantity(24, "h")),
                "fixture": "bench cure jig", "orientation": [0, 0, 1]}
        for label, item, applies in (("tool-free", hold, False), ("named tool", {**hold, "tools": ["spring clamp"]}, True),
                                     ("declared tool", {**hold, "tool_access": [{"tool": "spring clamp"}]}, True)):
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                documents = fx.tst_record()
                documents["assembly.yaml"]["operations"].append(item)
                record = intent.load_record(fx.write_record(Path(directory) / "hw", documents))
                access = {o.id for o in intent.required_checks(record) if o.family == "assembly.access"}
                self.assertIn("assembly.access@op:cure", access, "the obligation stays; not applicable is explicit")
                plan = {r.operation.id: r for r in ops.access_plan(ops.normalize(documents["assembly.yaml"]))}
                self.assertEqual(plan["cure"].not_applicable is None, applies)
                self.assertEqual(len(plan["cure"].tools), int(applies))
                self.assertIsNone(plan["fit_bearing"].not_applicable)

    def test_a_cure_figure_without_a_datasheet_source_fails(self) -> None:
        for name, cure in (("supplier time", {"time": sourced(24, "h", [{"kind": "supplier", "reference": "email"}])}),
                           ("unknown temperature", {"time": sourced(24, "h"),
                                                    "temperature": {"status": "unknown", "reason": "not looked up"}})):
            with self.subTest(name):
                bond_a, bond_b, _cure, install = self.items()
                report = ops.check_order(ops.normalize({"operations": [bond_a, bond_b, {**self.CURE, "cure": cure}, install]}),
                                         self.inventory())
                self.assertEqual([f["code"] for f in report.findings], ["assembly.cure_unsourced"])

    def test_a_hold_shorter_than_the_cure_time_fails(self) -> None:
        bond_a, bond_b, cure, install = self.items()
        short = {**cure, "duration": sourced(90, "min"), "cure": {"time": sourced(1, "d")}}
        report = ops.check_order(ops.normalize({"operations": [bond_a, bond_b, short, install]}), self.inventory())
        [finding] = report.findings
        self.assertEqual(finding["code"], "assembly.hold_short")
        self.assertIn("1.5 h, the cure time is 24 h", finding["measured"]["text"])

    def test_the_record_reader_refuses_a_cure_or_duration_of_the_wrong_dimension(self) -> None:
        def hold(**changes):
            return {"id": "cure", "kind": "hold", "part": "TST-0001", "duration": fx.known(fx.quantity(24, "h")),
                    "fixture": "bench cure jig", "orientation": [0, 0, 1],
                    "cure": {"time": fx.known(fx.quantity(24, "h")), "temperature": fx.known(fx.quantity(23, "deg_C"))},
                    **changes}

        cases = {
            "a duration in deg_C": ("operations/1/duration/value", hold(duration=fx.known(fx.quantity(24, "deg_C")))),
            "a cure time in mm": ("operations/1/cure/time/value", hold(cure={"time": fx.known(fx.quantity(24))})),
            "a cure temperature in h": ("operations/1/cure/temperature/value", hold(cure={
                "time": fx.known(fx.quantity(24, "h")), "temperature": fx.known(fx.quantity(23, "h"))})),
            "a well-formed hold": (None, hold()),
        }
        for label, (pointer, item) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                documents = fx.tst_record()
                documents["assembly.yaml"]["operations"].append(item)
                record = intent.load_record(fx.write_record(Path(directory) / "hw", documents))
                units = [p.pointer for p in record.problems if p.code == "intent.units"]
                self.assertEqual(units, [] if pointer is None else [pointer], [p.message for p in record.problems])

    def test_a_one_sided_interval_is_an_invalid_quantity_not_a_crash(self) -> None:
        for name, value in (("lower only", {"lower": 1, "units": "h"}), ("upper only", {"upper": 24, "units": "h"})):
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                hold = {"id": "cure", "kind": "hold", "part": "TST-0001", "fixture": "bench cure jig",
                        "orientation": [0, 0, 1], "duration": {**fx.known(fx.quantity(1, "h")), "value": value},
                        "cure": {"time": sourced(1, "h")}}
                documents = fx.tst_record()
                documents["assembly.yaml"]["operations"].append(hold)
                record = intent.load_record(fx.write_record(Path(directory) / "hw", documents))
                self.assertIn("operations/1/duration/value",
                              [p.pointer for p in record.problems if p.code == "intent.number"])
                report = ops.check_order(ops.normalize({"operations": [hold]}))
                self.assertIn("the hold lasts unknown", report.findings[0]["measured"]["text"])

    def test_a_cure_time_that_is_not_a_positive_time_fails_the_hold(self) -> None:
        for name, time in (("negative", sourced(-24, "h")), ("zero", sourced(0, "h")), ("a length", sourced(24, "mm"))):
            with self.subTest(name):
                bond_a, bond_b, cure, install = self.items()
                held = {**cure, "duration": sourced(1, "s"), "cure": {"time": time}}
                report = ops.check_order(ops.normalize({"operations": [bond_a, bond_b, held, install]}), self.inventory())
                self.assertEqual([f["code"] for f in report.findings], ["assembly.hold_short"])
                self.assertIn("not a known positive time", report.findings[0]["measured"]["text"])

    def test_a_hold_that_is_not_a_positive_time_is_short(self) -> None:
        bond_a, bond_b, cure, install = self.items()
        held = {**cure, "duration": sourced(-48, "h")}
        report = ops.check_order(ops.normalize({"operations": [bond_a, bond_b, held, install]}), self.inventory())
        [finding] = report.findings
        self.assertIn("the hold lasts unknown, the cure time is 24 h", finding["measured"]["text"])

    def test_an_uninstalled_subassembly_member_floats_in_its_sub_build(self) -> None:
        bond_a, _bond_b, cure, install = self.items()
        report = ops.check_order(ops.normalize({"operations": [bond_a, cure, install]}), self.inventory())
        [finding] = report.findings
        self.assertEqual((finding["code"], finding["subject"]), ("assembly.never_installed",
                                                                 {"PN": self.MAGNET, "occurrence": "@mag-b"}))
        self.assertIn("sub-build hanger has one base (the base is TST-0102)", finding["measured"]["text"])

    def test_bench_operations_are_normalized_strictly(self) -> None:
        for name, item in (("install of a part", {**self.INSTALL, "part": "TST-0102"}),
                           ("install inside a sub-build", {**self.INSTALL, "sub_build": "hanger"}),
                           ("duration on a join", {**self.bond("mag-a"), "duration": sourced(1, "h")}),
                           ("cure without a time", {**self.CURE, "cure": {"temperature": sourced(23, "deg_C")}}),
                           ("sub-build id", {**self.bond("mag-a"), "sub_build": "Hanger"})):
            with self.subTest(name), self.assertRaises(ops.OperationsError):
                ops.normalize({"operations": [item]})


class SchemaParity(unittest.TestCase):
    """assembly.schema.json#/$defs/assembly_operation and this module describe one set of operations. A kind
    added to one without the other (print_in_place is deferred, see the interfaces `print_in_place` block) fails here."""

    OPERATION = json.loads((SCRIPTS.parent / "schemas" / "assembly.schema.json").read_text(encoding="utf-8"))["$defs"]["assembly_operation"]

    def test_the_schema_kinds_are_the_modules_kinds(self) -> None:
        self.assertEqual(tuple(self.OPERATION["properties"]["kind"]["enum"]), ops.KINDS)

    def test_the_schema_requires_per_kind_what_the_module_requires(self) -> None:
        required: dict[str, tuple[str, ...]] = {}
        for rule in self.OPERATION["allOf"]:
            kinds = rule["if"].get("properties", {}).get("kind")
            needs = rule.get("then", {}).get("required")
            if kinds and needs:
                for kind in kinds.get("enum", [kinds.get("const")]):
                    required[kind] = tuple(needs)
        self.assertEqual(required, ops.REQUIRED)


if __name__ == "__main__":
    unittest.main()
