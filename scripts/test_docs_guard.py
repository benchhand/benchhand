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
LAYOUT = (
    "compares references/layout.md with layout.py's output; docs_only keeps that one file out"
    " of the safe set"
)

ALLOWED = [
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualifiedProfile.test_a_changed_decision_invalidates_the_published_bundle', 'DR-002.md', HW),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualifiedProfile.test_a_criterion_service_loads_and_convergence_make_strength_and_stiffness_pass', 'DR-002.md', HW),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualifiedProfile.test_an_approved_effective_isotropic_model_analyses_printed_material_at_its_orientation', 'DR-003.md', HW),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'QualifiedProfile.test_an_effective_isotropic_decision_needs_status_approved', 'DR-003.md', HW),
    Allow('skills/cad-analysis/tests/test_fea_conditions.py', 'SourcedFEACase.approve', '.md', HW),
    Allow('skills/cad-fits/test_stackup.py', 'StackPacketTests.test_a_justified_rss_publishes_its_statistical_outcome_and_approving_decision', 'DR-001.md', HW),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_opencode_receipt_uses_installer_project_paths', '.opencode/agents/cad-analyst.md', STUB, 2),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_opencode_receipt_uses_installer_project_paths', '.opencode/skills/cad-flow/SKILL.md', STUB, 2),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_receipt_must_include_installed_skills_and_agents_at_contract_paths', '.agents/skills/cad-flow/SKILL.md', STUB, 2),
    Allow('skills/cad-flow/tests/test_preflight.py', 'HarnessInstallTests.test_receipt_must_include_installed_skills_and_agents_at_contract_paths', '.codex/agents/cad-analyst.toml', STUB, 3),
    Allow('skills/cad-flow/tests/test_setup.py', 'PlanTests.test_install_uses_checkout_installer_with_project_relative_destination', 'harness', INSTALLER),
    Allow('skills/cad-flow/tests/test_setup.py', 'PlanTests.test_requested_harness_is_planned_but_not_implicit', 'harness/install.py', INSTALLER),
    Allow('skills/cad-recheck/tests/test_recheck.py', 'RecheckHookTests.provisional_gate', 'DR-001.md', HW),
    Allow('skills/cad-recheck/tests/test_recheck.py', 'RecheckHookTests.test_frozen_pre_freeze_stage_refuses_a_stray_file', 'hw/stray.md', HW),
    Allow('skills/cad-recheck/tests/test_recheck.py', 'RecheckHookTests.test_frozen_pre_freeze_stage_refuses_a_stray_file', 'stray.md', HW, 2),
    Allow('skills/cad-record/tests/fixtures/acceptance.py', 'decision', '.md', HW),
    Allow('skills/cad-record/tests/fixtures/manufacturing_intent.py', 'write_record', '.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.generate', 'index.md', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.setUp', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_a_hand_edited_generated_document_is_kept_unless_replace', 'docs', HW),
    Allow('skills/cad-record/tests/test_build_guide.py', 'BuildGuide.test_a_hand_edited_generated_document_is_kept_unless_replace', 'hw/docs/okh.toml was edited after build_guide.py generated it', HW),
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
    Allow('skills/cad-record/tests/test_design_rules.py', 'Retention.test_torque_and_retainer_decisions_exist', 'J-004 torque_path/decision DR-099 does not exist at decisions/DR-099.md', HW),
    Allow('skills/cad-record/tests/test_design_rules.py', 'Retention.test_torque_and_retainer_decisions_exist', 'interfaces/0/limits/press_fit/axial_retainer/decision DR-098 does not exist at decisions/DR-098.md', HW),
    Allow('skills/cad-record/tests/test_evidence.py', 'AcceptanceDigest.test_the_inventory_covers_evidence_and_sources_but_not_workflow_or_decisions', 'DR-001.md', HW),
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
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.setUp', 'docs/.DS_Store', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.setUp', 'docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.setUp', 'notes.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/docs/.DS_Store', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/docs/notes.md already exists: a collision', HW),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_every_stray_gets_its_destination_and_nothing_changes', 'hw/notes.md', HW, 2),
    Allow('skills/cad-record/tests/test_migration_plan.py', 'MigrationPlan.test_the_report_goes_to_stdout_or_a_path_under_cad_work_only', 'docs', HW, 2),
    Allow('skills/cad-record/tests/test_migrate.py', 'Version1Record.test_apply_writes_a_valid_version_2_record_of_explicit_unknowns', '.md', HW),
    Allow('skills/cad-record/tests/test_migrate.py', 'Version1Record.test_apply_writes_a_valid_version_2_record_of_explicit_unknowns', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Documents.guide', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Documents.test_the_package_okh_toml_describes_its_release', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Pruning.test_an_interrupted_prune_moves_back_a_version_that_became_a_milestone', 'DR-006.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Pruning.test_plan_keeps_frozen_change_baseline_and_decision_milestones', 'DR-006.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Pruning.test_yes_refuses_a_digest_that_a_fresh_plan_no_longer_has', 'DR-006.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_freeze_on_a_provisional_decision_refuses_the_release', 'DR-004.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_missing_decision_refuses', 'hw/decisions/DR-099.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_provisional_or_void_decision_never_approves_a_release', 'DR-006.md', HW, 3),
    Allow('skills/cad-record/tests/test_release.py', 'Refusal.test_a_provisional_or_void_decision_never_approves_a_release', 'hw/decisions/DR-006.md', HW, 2),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_complete_frozen_record_releases_a_verified_package', 'docs/inspection.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_released_original_may_go_but_its_package_copy_may_not', 'docs', HW, 3),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_released_original_may_go_but_its_package_copy_may_not', 'notes.md', HW, 3),
    Allow('skills/cad-record/tests/test_release.py', 'Release.test_a_released_original_may_go_but_its_package_copy_may_not', 'releases/A/docs/notes.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'DR-005.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'docs', HW),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'hw/docs/inspection.md', HW, 2),
    Allow('skills/cad-record/tests/test_release.py', 'frozen_lamp', 'inspection.md', HW),
    Allow('skills/cad-record/tests/test_release.py', 'waive_fdm_trials', 'hw/docs/inspection.md', HW),
    Allow('skills/cad-record/tests/test_schema_drawing.py', '<module>', 'decisions/DR-001.md', HW),
    Allow('skills/cad-record/tests/test_schema_drawing.py', 'DrawingRecordAdditions.write', 'DR-001.md', HW),
    Allow('skills/cad-record/tests/test_schema_manufacturing.py', 'SharedDefinitions.test_a_source_states_where_it_comes_from', 'docs/a.csv', HW),
    Allow('skills/cad-record/tests/test_schema_registry.py', '<module>', 'hw/docs/x.md', HW),
    Allow('skills/cad-record/tests/test_validate.py', 'ValidateProject.anchor_on', '.md', HW),
    Allow('skills/cad-record/tests/test_validate.py', 'ValidateProject.test_empty_template_passes_at_ground_but_requires_parts_at_layout', 'DR-001.md', HW),
    Allow('skills/cad-record/tests/test_validate.py', 'ValidateProject.test_only_the_users_approval_freezes_a_version_through_a_provisional_and_void_cycle', 'DR-004.md', HW),
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
