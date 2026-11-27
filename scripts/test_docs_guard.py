# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard for the docs-only rule (docs_only.py): no skill test may read a path the rule calls safe.

The guard is a name check over the string literals of the skill tests (``skills/*/tests/*.py`` and
``skills/*/test_*.py``). It flags every literal that
- ends in ``.md`` (any Markdown name, including ``"*.md"`` and ``".md"`` suffix tests), or
- names ``SKILL.md``, ``README`` or ``CHANGELOG``, or
- holds the path part ``references``, ``agents``, ``docs`` or ``harness``.
A flagged literal passes only when ALLOWED lists it for that file, that enclosing function or class
and that exact literal value, as many times as it occurs. A second ``"references"`` in the same
function, or a new ``"references/evidence.md"``, fails even when the file has other entries.

What it cannot see: a path built from a constant imported from a module the guard does not scan
(a skill script, for example), a path read through a variable that holds no flagged literal, and a
read in a skill script that a test runs. A constant defined in a scanned test file or fixture is
caught where it is defined. Treat the guard as a tripwire for the usual way a test names a file,
not as a proof that no test reads the safe set.
"""

import ast
import json
import re
import subprocess
import unittest
from collections import Counter
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
TEST_PATHSPECS = ["skills/*/tests/*.py", "skills/*/test_*.py"]
MENTION = re.compile(
    r"SKILL\.md|README|CHANGELOG|(?:^|/)(?P<part>references|agents|docs|harness)(?:/|$)"
)


def flagged(value: str) -> bool:
    return value.endswith(".md") or bool(MENTION.search(value))


class Scanner(ast.NodeVisitor):
    """Counts each flagged string literal by (enclosing function or class, value)."""

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.found: Counter[tuple[str, str]] = Counter()

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _enter  # type: ignore[assignment]

    def visit_Expr(self, node: ast.Expr) -> None:
        if not isinstance(node.value, ast.Constant):  # a bare string statement is a docstring
            self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and flagged(node.value):
            self.found[(".".join(self.scope) or "<module>", node.value)] += 1


def scan(source: str) -> Counter[tuple[str, str]]:
    scanner = Scanner()
    scanner.visit(ast.parse(source))
    return scanner.found


class Allow(NamedTuple):
    path: str
    scope: str
    literal: str
    why: str
    count: int = 1


def unlisted(path: str, source: str, allowed: list[Allow]) -> dict[tuple[str, str], int]:
    """The flagged (scope, literal) occurrences of a file beyond what ALLOWED lists for it."""
    budget = Counter({(a.scope, a.literal): a.count for a in allowed if a.path == path})
    extra = Counter(scan(source))
    extra.subtract(budget)
    return {key: count for key, count in extra.items() if count > 0}


# Why each entry is safe. Every literal below was read by hand.
HW = (
    "a file of a temporary hardware project (hw/docs, hw/decisions, generated guide output) that"
    " the test writes or reads under a temp directory; never a file of this repository"
)
STUB = "a stub receipt, skill or agent file the test writes into a temp project"
INSTALLER = (
    "names harness/install.py in the expected command; the installer is mocked. setup.py:170"
    " checks that the file exists, which is why docs_only keeps install.py out of the safe set"
)
INSTALL_FILES = (
    "reads harness/install.py, which is in docs_only.READ_BY_SKILL_TESTS, so its changes run the skill shards"
)
ROOT_NOTE = (
    "a file outside hw/ in a temporary project that the test stages to show --verify ignores it; never a"
    " file of this repository"
)
TMP = (
    "a file of a decision-record directory the test creates under a temporary directory; never a file of this"
    " repository"
)
LAYOUT = (
    "compares references/layout.md with layout.py's output; docs_only keeps that one file out"
    " of the safe set"
)

LAUNCH = (
    "reads skills/cad-concept/SKILL.md for its serve.py launch command and runs it against a temp scene directory;"
    " docs_only keeps that one file out of the safe set"
)
ALLOWED = [
    Allow('skills/cad-concept/tests/test_concept.py', 'skill_launch_args', 'SKILL.md', LAUNCH),
    Allow('skills/cad-design/tests/test_mfg_corpus.py', 'DocLeakTests', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-design/tests/test_mfg_corpus.py', 'DocLeakTests', 'index.md', HW),
    Allow('skills/cad-design/tests/test_mfg_corpus.py', 'DocLeakTests.documents.capture', 'docs', HW),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualifiedProfile.test_an_effective_isotropic_decision_the_reader_refuses_or_in_the_old_form_is_no_evidence', 'DR-003.md', HW, 2),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualifiedProfile.test_an_effective_isotropic_decision_the_reader_refuses_or_in_the_old_form_is_no_evidence', 'DR-003-model.md', HW),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualificationBypasses.test_only_a_valid_accepted_madr_record_counts', 'DR-002.md', HW),
    Allow('skills/cad-flow/tests/test_flow_profile.py', 'Record.test_an_index_that_cannot_be_updated_removes_the_new_record_and_restores_the_product', 'DR-*.md', HW),
    Allow('skills/cad-flow/tests/test_flow_profile.py', 'Record.test_an_index_that_cannot_be_updated_removes_the_new_record_and_restores_the_product', 'index.md', HW, 2),
    Allow('skills/cad-flow/tests/test_flow_profile.py', 'Record.test_record_refuses_a_decision_that_exists_and_changes_nothing', 'DR-*.md', HW),
    Allow('skills/cad-flow/tests/test_flow_profile.py', 'Record.test_record_writes_an_accepted_madr_decision', 'index.md', HW),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_opencode_receipt_uses_installer_project_paths', '.opencode/agents/cad-analyst.md', STUB, 2),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_opencode_receipt_uses_installer_project_paths', '.opencode/skills/cad-flow/SKILL.md', STUB, 2),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_receipt_must_include_installed_skills_and_agents_at_contract_paths', '.agents/skills/cad-flow/SKILL.md', STUB, 2),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_receipt_must_include_installed_skills_and_agents_at_contract_paths', '.codex/agents/cad-analyst.toml', STUB, 3),
    Allow('skills/cad-flow/tests/test_setup.py', 'PlanTests.test_install_uses_checkout_installer_with_project_relative_destination', 'harness', INSTALLER),
    Allow('skills/cad-flow/tests/test_setup.py', 'PlanTests.test_requested_harness_is_planned_but_not_implicit', 'harness/install.py', INSTALLER),
    Allow('skills/cad-model/tests/test_cad_model.py', 'RebuildTriggerTests.setUpClass', 'harness', INSTALL_FILES),
    Allow('skills/cad-recheck/tests/test_recheck.py', 'RecheckHookTests.test_frozen_pre_freeze_stage_refuses_a_stray_file', 'hw/stray.md', HW),
    Allow('skills/cad-recheck/tests/test_recheck.py', 'RecheckHookTests.test_verify_passes_a_commit_that_stages_no_hw_change_and_checks_one_that_does', 'notes.md', ROOT_NOTE, 2),
    Allow('skills/cad-recheck/tests/test_recheck.py', 'RecheckHookTests.test_frozen_pre_freeze_stage_refuses_a_stray_file', 'stray.md', HW, 2),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.generate', 'index.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.setUp', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_a_hand_edited_generated_document_is_kept_unless_replace', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_a_hand_edited_generated_document_is_kept_unless_replace', 'hw/docs/okh.toml was edited after build_guide.py generated it', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_a_hold_quantity_that_does_not_parse_is_a_guide_input_error', 'index.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_a_partial_license_prints_human_owned_never_none', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_canonical_docs_output_generates_okh_and_din_from_record', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_canonical_docs_output_generates_okh_and_din_from_record', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_canonical_docs_output_generates_okh_and_din_from_record', 'index.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_documented_docs_argument_is_supported', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_documented_docs_argument_is_supported', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_documented_docs_argument_is_supported', 'index.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_evidence_keeps_its_text_and_links_files_and_bundle_directories', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_evidence_keeps_its_text_and_links_files_and_bundle_directories', 'docs', HW, 2),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_evidence_keeps_its_text_and_links_files_and_bundle_directories', 'inspection.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_evidence_keeps_its_text_and_links_files_and_bundle_directories', 'see hw/docs/inspection.md.', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_evidence_keeps_its_text_and_links_files_and_bundle_directories', '| see [hw/docs/inspection.md](../../hw/docs/inspection.md). |', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_hw_docs_files_that_build_guide_did_not_generate_are_kept_unless_replace', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_hw_docs_files_that_build_guide_did_not_generate_are_kept_unless_replace', 'hw/docs/index.md was not generated by build_guide.py', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_hw_docs_files_that_build_guide_did_not_generate_are_kept_unless_replace', 'index.md', HW, 3),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_missing_license_stays_human_owned_not_inferred_from_license_file', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_missing_release_manifest_is_named_in_guide_and_din', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_missing_version_manifest_fails_without_output', 'index.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_okh_paths_are_relative_paths_from_its_folder_not_links', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_rev_prepares_the_documents_for_that_release_on_its_given_date', 'din-spec-3105-1-checklist.md', HW),
    Allow('skills/cad-record/tests/test_decision_records.py', 'FindTests.test_find_decision_reads_only_the_record_it_names', 'DR-002-bad.md', TMP, 2),
    Allow('skills/cad-record/tests/test_decision_records.py', 'FindTests.test_find_decision_refuses_two_files_with_one_id', 'DR-001-a.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'FindTests.test_find_decision_refuses_two_files_with_one_id', 'DR-001-b.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'IndexTests.test_a_malformed_existing_record_stops_the_write_and_changes_nothing', 'DR-001-bad.md', TMP, 3),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_decision_file_with_a_non_madr_name_is_refused', 'DR-001-Upper.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_decision_file_with_a_non_madr_name_is_refused', 'DR-001-x-.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_decision_file_with_a_non_madr_name_is_refused', 'DR-001.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_decision_file_with_a_non_madr_name_is_refused', 'DR-1-x.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_non_utf8_file_is_refused', 'DR-001-a.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_superseded_by_that_names_no_record_is_refused_by_the_directory_read', 'DR-001-a.md', TMP, 2),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_superseded_by_that_names_no_record_is_refused_by_the_directory_read', 'DR-002-b.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_malformed_input_is_refused_with_the_file_named', 'DR-001-case.md', TMP, 2),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_read_decisions_returns_every_record_in_number_order', 'DR-002-a.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_read_decisions_returns_every_record_in_number_order', 'DR-010-b.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_the_file_number_names_the_record_not_the_title', 'DR-007-a-b.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_two_records_with_one_number_are_refused', 'DR-001-a.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_two_records_with_one_number_are_refused', 'DR-001-b.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'WriteTests.test_numbers_continue_after_the_highest_existing_record', 'DR-002-use-steel.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'WriteTests.test_superseded_by_must_name_an_existing_other_record', 'DR-*.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'WriteTests.test_the_first_record_is_numbered_one_and_named_after_its_title', 'DR-001-press-fit-pivot-pins.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_decision_makers_are_optional_and_read_as_none_when_absent', 'DR-001-a.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_an_old_form_decision_is_refused_naming_the_expected_shape', 'DR-001.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_slugged_file_without_front_matter_is_refused_naming_the_expected_shape', 'DR-001-gate.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'WriteTests.test_a_malformed_gate_is_refused_naming_the_file_and_the_key', 'DR-001-gate.md', TMP),
    Allow('skills/cad-record/tests/test_design_rules.py', 'Retention.test_torque_and_retainer_decisions_exist', 'J-004 torque_path/decision DR-099 has no record: expected hw/decisions/DR-099-<slug>.md', HW),
    Allow('skills/cad-record/tests/test_design_rules.py', 'Retention.test_torque_and_retainer_decisions_exist', 'interfaces/0/limits/press_fit/axial_retainer/decision DR-098 has no record: expected hw/decisions/DR-098-<slug>.md', HW),
    Allow('skills/cad-record/tests/test_evidence.py', 'UnknownNeverPasses.test_a_verification_csv_pass_is_not_evidence', 'hw/docs/x.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_layout_md_is_generated_from_layout_json', 'layout.md', LAYOUT),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_layout_md_is_generated_from_layout_json', 'references', LAYOUT),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_layout_md_is_generated_from_layout_json', 'regenerate: python3 cad/skills/cad-record/scripts/layout.py --markdown > cad/skills/cad-record/references/layout.md', LAYOUT),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_wrong_pn_version_or_nested_name_is_rejected', 'hw/decisions/DR-01.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_wrong_pn_version_or_nested_name_is_rejected', 'hw/docs/flat.step', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_wrong_pn_version_or_nested_name_is_rejected', 'hw/docs/notes/export.py', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'TreeAudit.test_exceptions_rules_and_strays', 'docs/.DS_Store', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'TreeAudit.test_exceptions_rules_and_strays', 'hw/docs/.DS_Store', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'TreeAudit.test_exceptions_rules_and_strays', 'hw/docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'TreeAudit.test_exceptions_rules_and_strays', 'hw/notes.md', HW, 2),
    Allow('skills/cad-record/tests/test_layout.py', 'TreeAudit.test_exceptions_rules_and_strays', 'notes.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_lamp_with_canonical_outputs_validates_clean_before_and_after_freeze', 'assembly-notes.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_lamp_with_canonical_outputs_validates_clean_before_and_after_freeze', 'docs', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_symlink_escape_is_an_error_at_every_stage', 'docs', HW, 4),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_symlink_escape_is_an_error_at_every_stage', 'docs/escape', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_symlink_escape_is_an_error_at_every_stage', 'docs/inside.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_symlink_escape_is_an_error_at_every_stage', 'docs/outside.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_symlink_escape_is_an_error_at_every_stage', 'inside.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_symlink_escape_is_an_error_at_every_stage', 'outside.md', HW, 2),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_unsafe_names_are_errors_before_freeze', 'bad\nname.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_unsafe_names_are_errors_before_freeze', 'docs', HW, 2),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_file_refuses_a_bad_id_and_two_files_with_one_id', 'DR-001-also.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-004-bushings-and-rings.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'index.md', HW, 2),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-001.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-001-Upper.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-001-.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-1-x.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-1000-x.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'DR-001-x/y.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_files_are_slugged_madr_records_and_the_index', 'notes.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_file_finds_the_slugged_file_of_an_id', 'DR-001.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_file_finds_the_slugged_file_of_an_id', 'DR-002-press-fit-pins.md', HW, 3),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_file_finds_the_slugged_file_of_an_id', 'DR-0021-other.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'LayoutData.test_decision_file_refuses_a_bad_id_and_two_files_with_one_id', 'DR-001-one.md', HW),
    Allow('skills/cad-record/tests/test_layout.py', 'ValidatorLayout.test_frozen_anchor_must_name_the_exact_built_version', 'flow/change/decision DR-009 has no record: expected hw/decisions/DR-009-<slug>.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.setUp', 'docs/.DS_Store', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.setUp', 'docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.setUp', 'notes.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/docs/.DS_Store', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/docs/notes.md already exists: a collision', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/notes.md', HW, 2),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_the_report_goes_to_stdout_or_a_path_under_cad_work_only', 'docs', HW, 2),
    Allow('skills/cad-record/tests/test_release.py', 'Pruning.test_a_stray_decision_file_that_names_a_version_still_keeps_it', 'DR-007-Bad_Name.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Pruning.test_a_stray_decision_file_that_names_a_version_still_keeps_it', 'DR-007.md', HW),
    Allow('skills/cad-record/tests/test_manufacturing_intent.py', 'Normalization.test_a_decision_the_layout_cannot_resolve_is_a_reference_problem_not_a_crash', 'DR-009-second.md', HW),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_front_matter_key_given_twice_is_refused_naming_the_key', 'DR-001-twice.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_sequence_or_mapping_as_a_front_matter_key_is_refused_naming_the_file', 'DR-001-unhashable.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_an_impossible_calendar_date_is_refused_naming_the_file', 'DR-001-date.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_front_matter_the_yaml_constructors_cannot_convert_is_refused_naming_the_file', 'DR-001-yaml.md', TMP),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_a_huge_integer_key_or_status_is_refused_with_a_bounded_message', 'DR-001-huge.md', TMP, 2),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_an_oversized_diagnostic_value_is_cut_in_the_message_naming_the_file', 'DR-001-long.md', TMP, 2),
    Allow('skills/cad-record/tests/test_decision_records.py', 'ReadTests.test_unknown_front_matter_keys_of_mixed_types_are_refused_naming_the_file', 'DR-001-keys.md', TMP),
    Allow('skills/cad-record/tests/test_release.py', 'Documents.guide', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Documents.test_the_package_okh_toml_describes_its_release', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Documents.test_a_next_day_repair_keeps_the_release_date', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_complete_frozen_record_releases_a_verified_package', 'docs/inspection.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_released_original_may_go_but_its_package_copy_may_not', 'docs', HW, 3),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_released_original_may_go_but_its_package_copy_may_not', 'notes.md', HW, 3),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_released_original_may_go_but_its_package_copy_may_not', 'releases/A/docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'hw/docs/inspection.md', HW, 2),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'inspection.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'waive_fdm_trials', 'hw/docs/inspection.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_missing_decision_refuses', 'DR-099 has no record: expected hw/decisions/DR-099-<slug>.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_only_an_accepted_decision_approves_a_release', 'hw/decisions/DR-006-test-decision-dr-006.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_release_decision_the_reader_refuses_or_in_the_old_form_is_no_approval', 'DR-006.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_release_decision_the_reader_refuses_or_in_the_old_form_is_no_approval', 'DR-006 has no record: expected hw/decisions/DR-006-<slug>.md', HW),
    Allow('skills/cad-record/tests/test_schema_drawing.py', '<module>', 'decisions/DR-001-test-decision-dr-001.md', HW),
    Allow('skills/cad-record/tests/test_schema_manufacturing.py', 'SharedDefinitions.test_a_source_states_where_it_comes_from', 'docs/a.csv', HW),
    Allow('skills/cad-record/tests/test_schema_registry.py', '<module>', 'hw/docs/x.md', HW),
    Allow('skills/cad-record/tests/test_validate.py', 'ValidateProject.test_an_old_form_decision_file_is_no_record', 'DR-009.md', HW),
    Allow('skills/cad-record/tests/test_validate.py', 'ValidateProject.test_an_old_form_decision_file_is_no_record', 'flow/frozen/decision DR-009 has no record: expected hw/decisions/DR-009-<slug>.md', HW),
]


def tracked_tests() -> list[str]:
    return subprocess.run(
        ["git", "ls-files", *TEST_PATHSPECS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


class ScannerTests(unittest.TestCase):
    """Each hole a reviewer found, as a synthetic test source the guard must flag."""

    def flagged_in(self, source: str) -> set[str]:
        return {literal for _, literal in scan(source)}

    def test_a_test_that_reads_a_skill_md_is_caught(self) -> None:
        source = 'from pathlib import Path\nTEXT = Path("skills/x/SKILL.md").read_text()\n'
        self.assertEqual(self.flagged_in(source), {"skills/x/SKILL.md"})

    def test_root_markdown_under_any_name_is_caught(self) -> None:
        source = 'def test_x():\n    return (ROOT / "CONTRIBUTING.md").read_text()\n'
        self.assertEqual(self.flagged_in(source), {"CONTRIBUTING.md"})

    def test_markdown_globs_are_caught(self) -> None:
        source = 'def test_x():\n    a = list(ROOT.glob("*.md"))\n    b = list(ROOT.rglob("**/*.md"))\n'
        self.assertEqual(self.flagged_in(source), {"*.md", "**/*.md"})

    def test_a_suffix_test_for_markdown_is_caught(self) -> None:
        self.assertEqual(self.flagged_in('ok = path.suffix == ".md"\n'), {".md"})

    def test_a_joined_path_part_is_caught(self) -> None:
        source = 'T = (SKILL / "references" / "layout.md").read_text()\n'
        self.assertEqual(self.flagged_in(source), {"references", "layout.md"})

    def test_a_path_inside_one_string_is_caught(self) -> None:
        self.assertEqual(
            self.flagged_in('p = "skills/x/references/y.png"\n'),
            {"skills/x/references/y.png"},
        )

    def test_a_constant_defined_in_a_scanned_file_is_caught_where_it_is_defined(
        self,
    ) -> None:
        self.assertEqual(self.flagged_in('PLAN = "docs/plan.json"\n'), {"docs/plan.json"})

    def test_a_path_from_an_imported_constant_is_a_known_blind_spot(self) -> None:
        source = "from consts import GUIDE\nTEXT = GUIDE.read_text()\n"
        self.assertEqual(self.flagged_in(source), set())

    def test_prose_a_comment_and_an_unrelated_name_are_not_flagged(self) -> None:
        source = '"""Reads SKILL.md."""\n# see docs/\ny = "mydocs"\nz = "hw/decisions/DR-1.json"\n'
        self.assertEqual(self.flagged_in(source), set())


FLOW_DOCS = tuple(ROOT / "skills" / "cad-flow" / rel
                  for rel in ("SKILL.md", "references/flow.md", "references/manufacturing-selection.md"))
RULES_INVOCATION = 'uv run --script "$CAD_SKILLS/cad-record/scripts/project_scope.py" rules'
SERVICE_CLASSES = tuple(
    name for name in json.loads(
        (ROOT / "skills/cad-record/schemas/manufacturing.schema.json").read_text(encoding="utf-8")
    )["$defs"]["service_class"]["enum"] if name != "unknown"
)


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
DELIMITER_CELL = re.compile(r"^:?-+:?$")


def table_cells(line: str) -> list[str]:
    """The cells of a Markdown table row: split at pipes that no backslash escapes, leading and trailing pipe optional."""
    cells = re.split(r"(?<!\\)\|", line.strip())
    if line.strip().startswith("|"):
        cells = cells[1:]
    if line.strip().endswith("|") and not line.strip().endswith("\\|"):
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def is_delimiter_row(line: str) -> bool:
    cells = table_cells(line)
    return "|" in line and bool(cells) and all(DELIMITER_CELL.match(cell) for cell in cells)


def mask_fences(lines: list[str]) -> list[str]:
    """`lines` with every code fence, its marker lines included, replaced by blank lines."""
    masked, fence = [], None
    for line in lines:
        marker = FENCE.match(line)
        closes = bool(fence and marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence)
                      and not line.strip()[len(marker.group(1)):].strip())
        if fence is None and marker:
            fence = marker.group(1)
        elif closes:
            fence = None
        masked.append("" if fence is not None or marker else line)
    return masked


def table_rows(lines: list[str]) -> list[int]:
    """The indexes of the header and body rows of every GitHub table block in `lines`: a header row, then a delimiter
    row with as many cells, then body rows up to a blank line or a line without a pipe."""
    rows, index = [], 0
    while index < len(lines):
        is_table = ("|" in lines[index] and index + 1 < len(lines) and is_delimiter_row(lines[index + 1])
                    and len(table_cells(lines[index])) == len(table_cells(lines[index + 1])))
        if not is_table:
            index += 1
            continue
        rows.append(index)
        index += 2
        while index < len(lines) and "|" in lines[index]:
            rows.append(index)
            index += 1
    return rows


def class_table_cells(text: str, classes: tuple[str, ...] = SERVICE_CLASSES) -> list[tuple[int, str]]:
    """The (line, cell) pairs of GitHub Markdown table blocks whose cell is a service class name, with or without
    backticks. Code fences are skipped."""
    lines = mask_fences(text.splitlines())
    return [(number + 1, cell) for number in table_rows(lines) for cell in table_cells(lines[number])
            if cell.strip("`").strip() in classes]


class ServiceClassDocTests(unittest.TestCase):
    """The flow docs point to `project_scope.py rules` and keep no service-class table (design T3, PR 3). They are
    in the docs-only safe set, so this check lives here, in the lane a docs-only PR still runs."""

    def test_a_table_cell_that_is_a_class_name_is_found_with_or_without_backticks(self) -> None:
        text = ("| Description | `service_class` |\n|---|---|\n| Fit prototype | `fit_assembly` |\n"
                "| production | Record the standards |\n|  safety_critical  | x |\n| a production line | y |\n")
        self.assertEqual(class_table_cells(text), [(3, "`fit_assembly`"), (4, "production"), (5, "safety_critical")])

    def test_a_table_without_leading_pipes_is_found(self) -> None:
        text = "Use | Class\n--- | :---:\nfit | `fit_assembly`\ndemo | functional_demo\nrest | x\n\nafter | production\n"
        self.assertEqual(class_table_cells(text), [(3, "`fit_assembly`"), (4, "functional_demo")])

    def test_a_class_in_the_header_row_is_found(self) -> None:
        self.assertEqual(class_table_cells("| production | x |\n|---|---|\n| a | b |\n"), [(1, "production")])

    def test_a_pipe_example_in_a_code_fence_is_not_a_table(self) -> None:
        text = ("```markdown\n| Class | Use |\n|---|---|\n| `production` | end use |\n```\n"
                "~~~\n| a | b |\n|---|---|\n| fit_assembly | c |\n~~~\n")
        self.assertEqual(class_table_cells(text), [])

    def test_a_table_after_a_closed_fence_is_found(self) -> None:
        text = "```\ncode\n```\n| a | b |\n|---|---|\n| production | c |\n"
        self.assertEqual(class_table_cells(text), [(6, "production")])

    def test_pipe_lines_without_a_delimiter_row_and_prose_are_not_a_table(self) -> None:
        self.assertEqual(class_table_cells("The `fit_assembly` class.\n- production\n| production | x |\n"), [])
        self.assertEqual(class_table_cells("production\n---\n"), [])

    def test_an_escaped_pipe_does_not_split_a_cell(self) -> None:
        self.assertEqual(class_table_cells("| a | b |\n|---|---|\n| fit \\| production | x |\n"), [])

    def test_no_flow_doc_has_a_table_row_that_names_a_service_class(self) -> None:
        for doc in FLOW_DOCS:
            with self.subTest(doc=doc.name):
                self.assertEqual(class_table_cells(doc.read_text(encoding="utf-8")), [])

    def test_each_flow_doc_gives_the_runnable_rules_invocation(self) -> None:
        for doc in FLOW_DOCS:
            with self.subTest(doc=doc.name):
                self.assertIn(RULES_INVOCATION, doc.read_text(encoding="utf-8"))

    def test_no_flow_doc_names_the_command_by_a_relative_path(self) -> None:
        """`cad-record/scripts/project_scope.py rules` alone exits 127 from the project directory."""
        for doc in FLOW_DOCS:
            with self.subTest(doc=doc.name):
                self.assertNotRegex(doc.read_text(encoding="utf-8"), r"project_scope\.py rules")

    def test_the_gate_summary_rule_restates_the_class_rules_with_the_invocation(self) -> None:
        lines = FLOW_DOCS[0].read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("gate summary" in line and "restate" in line and RULES_INVOCATION in line
                            for line in lines))


class AllowlistTests(unittest.TestCase):
    PATH = "skills/cad-record/tests/test_layout.py"
    BASE = (
        "class LayoutData:\n"
        "    def test_layout_md(self):\n"
        '        return (SKILL / "references" / "layout.md").read_text()\n'
    )
    ALLOWED = [
        Allow(PATH, "LayoutData.test_layout_md", "references", "why"),
        Allow(PATH, "LayoutData.test_layout_md", "layout.md", "why"),
    ]

    def test_the_listed_occurrences_pass(self) -> None:
        self.assertEqual(unlisted(self.PATH, self.BASE, self.ALLOWED), {})

    def test_a_new_file_read_behind_an_already_listed_token_fails(self) -> None:
        source = self.BASE + '        more = (SKILL / "references" / "evidence.md").read_text()\n'
        self.assertEqual(
            unlisted(self.PATH, source, self.ALLOWED),
            {("LayoutData.test_layout_md", "evidence.md"): 1,
             ("LayoutData.test_layout_md", "references"): 1},
        )

    def test_a_second_occurrence_of_a_listed_literal_fails(self) -> None:
        source = self.BASE + '        again = SKILL / "references"\n'
        self.assertEqual(
            unlisted(self.PATH, source, self.ALLOWED),
            {("LayoutData.test_layout_md", "references"): 1},
        )

    def test_a_listed_literal_in_another_function_or_file_fails(self) -> None:
        moved = self.BASE.replace("test_layout_md", "test_other")
        self.assertEqual(len(unlisted(self.PATH, moved, self.ALLOWED)), 2)
        self.assertEqual(len(unlisted("skills/x/tests/test_y.py", self.BASE, self.ALLOWED)), 2)


class RepoGuardTests(unittest.TestCase):
    def test_no_skill_test_names_a_safe_set_path_beyond_the_allowlist(self) -> None:
        tracked = tracked_tests()
        self.assertTrue(tracked)
        found = {}
        for path in tracked:
            extra = unlisted(path, (ROOT / path).read_text(encoding="utf-8"), ALLOWED)
            if extra:
                found[path] = extra
        self.assertEqual(
            found,
            {},
            "a skill test names a path the docs-only rule treats as safe: read it, then list it"
            " in ALLOWED with the reason, or take the path out of docs_only.is_safe",
        )

    def test_every_allowlist_entry_still_matches_and_has_a_reason(self) -> None:
        scanned = {
            path: scan((ROOT / path).read_text(encoding="utf-8"))
            for path in {entry.path for entry in ALLOWED}
        }
        for entry in ALLOWED:
            with self.subTest(path=entry.path, scope=entry.scope, literal=entry.literal):
                self.assertTrue(entry.why.strip())
                self.assertEqual(scanned[entry.path][(entry.scope, entry.literal)], entry.count)


if __name__ == "__main__":
    unittest.main()
