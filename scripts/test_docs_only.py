import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docs_only  # noqa: E402
from docs_only import Change  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def edits(*paths: str) -> list[Change]:
    return [Change(path) for path in paths]


class ClassifierTests(unittest.TestCase):
    def test_every_safe_class_is_safe(self) -> None:
        safe = [
            "README.md",
            "CONTRIBUTING.md",
            "CHANGELOG.md",
            "CHANGELOG",
            "docs/walkthrough.md",
            "docs/deep/diagram.png",
            "agents/cad-analyst.md",
            "harness/generate.py",
            "harness/test_sources.py",
            "skills/cad-flow/SKILL.md",
            "skills/cad-model/references/outputs.md",
            "skills/cad-model/references/layout.md",  # only cad-record's layout.md is excepted
        ]
        for path in safe:
            with self.subTest(path=path):
                self.assertTrue(docs_only.is_safe(path))

    def test_the_two_exceptions_and_near_misses_are_not_safe(self) -> None:
        unsafe = [
            "harness/install.py",  # cad-flow's setup test needs the file to exist
            "skills/cad-record/references/layout.md",  # test_layout compares it to layout.py
            "skills/cad-model/references/sub/deep.md",  # only references/*.md is in the set
            "skills/cad-model/references/data.json",
            "skills/cad-model/scripts/SKILL.md",
            "skills/cad-model/assets/docs-templates/x.md",  # build_guide.py reads templates
            "skills/cad-model/cad_model.py",
            "skills/SKILL.md",
            "agents/cad-analyst.toml",
            "agents/sub/x.md",
            "scripts/ci_shards.py",
            ".github/workflows/ci.yml",
            "examples/lamp-arm/hw/product.yaml",
            "LICENSE",
            "mypy.ini",
            "other/README.md",
            "",
        ]
        for path in unsafe:
            with self.subTest(path=path):
                self.assertFalse(docs_only.is_safe(path))


class SkipTests(unittest.TestCase):
    def test_all_safe_changes_skip_the_skill_shards(self) -> None:
        self.assertTrue(
            docs_only.skips_skills(edits("README.md", "skills/cad-flow/SKILL.md"))
        )

    def test_one_unsafe_path_among_safe_ones_runs_everything(self) -> None:
        self.assertFalse(
            docs_only.skips_skills(edits("README.md", "skills/cad-flow/scripts/run.py"))
        )

    def test_an_empty_diff_runs_everything(self) -> None:
        self.assertFalse(docs_only.skips_skills([]))

    def test_added_and_deleted_regular_files_are_plain(self) -> None:
        added = Change("docs/new.md", "A", "000000", "100644")
        deleted = Change("docs/old.md", "D", "100755", "000000")
        self.assertTrue(docs_only.skips_skills([added, deleted]))

    def test_a_symlink_submodule_or_mode_change_on_a_safe_path_runs_everything(
        self,
    ) -> None:
        cases = {
            "symlink edited": Change("docs/a.md", "M", "120000", "120000"),
            "symlink added": Change("docs/a.md", "A", "000000", "120000"),
            "file became a symlink": Change("README.md", "T", "100644", "120000"),
            "symlink became a file": Change("README.md", "T", "120000", "100644"),
            "submodule": Change("docs/sub", "M", "160000", "160000"),
            "exec bit set": Change("harness/run.sh", "M", "100644", "100755"),
            "unmerged": Change("README.md", "U", "100644", "100644"),
        }
        for case, change in cases.items():
            with self.subTest(case=case):
                self.assertFalse(docs_only.skips_skills([Change("README.md"), change]))


class DecideTests(unittest.TestCase):
    def test_a_pull_request_of_safe_paths_skips(self) -> None:
        self.assertTrue(
            docs_only.decide("pull_request", "b", "h", lambda b, h: edits("docs/a.md"))
        )

    def test_a_pull_request_with_a_code_path_runs_everything(self) -> None:
        self.assertFalse(
            docs_only.decide(
                "pull_request", "b", "h", lambda b, h: edits("docs/a.md", "scripts/x.py")
            )
        )

    def test_a_push_runs_everything_without_reading_a_diff(self) -> None:
        def diff(base: str, head: str) -> list[Change]:
            raise AssertionError("a push needs no diff")

        self.assertFalse(docs_only.decide("push", "b", "h", diff))

    def test_a_missing_sha_runs_everything(self) -> None:
        for base, head in (("", "h"), ("b", ""), ("", "")):
            with self.subTest(base=base, head=head):
                self.assertFalse(
                    docs_only.decide(
                        "pull_request", base, head, lambda b, h: edits("docs/a.md")
                    )
                )

    def test_a_diff_error_runs_everything(self) -> None:
        def diff(base: str, head: str) -> list[Change]:
            raise docs_only.DiffError("no merge base")

        self.assertFalse(docs_only.decide("pull_request", "b", "h", diff))

    def test_a_missing_git_binary_runs_everything(self) -> None:
        def diff(base: str, head: str) -> list[Change]:
            raise FileNotFoundError("git")

        self.assertFalse(docs_only.decide("pull_request", "b", "h", diff))


class ParseRawTests(unittest.TestCase):
    def test_a_raw_record_yields_modes_status_and_path(self) -> None:
        raw = (
            ":100644 120000 aaaa bbbb T\0docs/a.md\0"
            ":000000 100644 0000 cccc A\0README.md\0"
        )
        self.assertEqual(
            docs_only.parse_raw(raw),
            [
                Change("docs/a.md", "T", "100644", "120000"),
                Change("README.md", "A", "000000", "100644"),
            ],
        )

    def test_a_record_that_cannot_be_read_is_a_diff_error(self) -> None:
        with self.assertRaises(docs_only.DiffError):
            docs_only.parse_raw("garbage\0docs/a.md\0")


class GitDiffTests(unittest.TestCase):
    def repo(self, tmp: str) -> Path:
        repo = Path(tmp)
        run("git", "init", "-q", "-b", "main", cwd=repo)
        run("git", "config", "user.email", "t@example.com", cwd=repo)
        run("git", "config", "user.name", "t", cwd=repo)
        (repo / "harness").mkdir()
        (repo / "harness" / "install.py").write_text("print('x')\n" * 20)
        (repo / "README.md").write_text("hello\n")
        run("git", "add", "-A", cwd=repo)
        run("git", "commit", "-qm", "base", cwd=repo)
        run("git", "checkout", "-qb", "topic", cwd=repo)
        return repo

    def test_a_rename_lists_both_paths_so_leaving_a_guarded_path_is_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            run("git", "mv", "harness/install.py", "harness/installer.py", cwd=repo)
            run("git", "commit", "-qam", "rename", cwd=repo)
            changed = docs_only.git_diff(repo, "main", "topic")
            self.assertEqual(
                sorted(change.path for change in changed),
                ["harness/install.py", "harness/installer.py"],
            )
            self.assertFalse(docs_only.skips_skills(changed))

    def test_replacing_a_markdown_file_with_a_symlink_to_code_runs_everything(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            (repo / "README.md").unlink()
            (repo / "README.md").symlink_to("harness/install.py")
            run("git", "commit", "-qam", "link", cwd=repo)
            changed = docs_only.git_diff(repo, "main", "topic")
            self.assertEqual([c.path for c in changed], ["README.md"])
            self.assertFalse(docs_only.skips_skills(changed))

    def test_a_plain_markdown_edit_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            (repo / "README.md").write_text("changed\n")
            run("git", "commit", "-qam", "edit", cwd=repo)
            changed = docs_only.git_diff(repo, "main", "topic")
            self.assertTrue(docs_only.skips_skills(changed))

    def test_a_bad_revision_is_a_diff_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run("git", "init", "-q", cwd=Path(tmp))
            with self.assertRaises(docs_only.DiffError):
                docs_only.git_diff(Path(tmp), "0" * 40, "1" * 40)


def evaluate(expression: str, result: str, skip: str) -> bool:
    """Evaluate the one boolean expression the skills job's SKIP variable may hold."""
    body = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    if not re.fullmatch(r"[\w.\s'=&!]+", body):
        raise AssertionError(f"an expression this test cannot evaluate: {body}")
    python = (
        body.replace("&&", " and ")
        .replace("needs.changes.outputs.skip", "OUTPUT")
        .replace("needs.changes.result", "RESULT")
    )
    return bool(eval(python, {"__builtins__": {}}, {"OUTPUT": skip, "RESULT": result}))


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        cls.job = workflow["jobs"]["skills"]

    def test_the_shards_skip_only_when_the_gate_succeeded_and_said_true(self) -> None:
        expression = self.job["env"]["SKIP"]
        cases = {
            ("success", "true"): True,
            ("failure", "true"): False,  # the gate wrote its output, then a later step failed
            ("cancelled", "true"): False,
            ("skipped", "true"): False,
            ("success", "false"): False,
            ("success", ""): False,
            ("failure", ""): False,
        }
        for (result, skip), expected in cases.items():
            with self.subTest(result=result, skip=skip):
                self.assertEqual(evaluate(expression, result, skip), expected)

    def test_the_job_runs_after_the_gate_even_when_the_gate_failed(self) -> None:
        self.assertEqual(self.job["needs"], "changes")
        self.assertIn("!cancelled()", self.job["if"])

    def test_every_step_follows_the_one_skip_variable(self) -> None:
        notices = []
        for step in self.job["steps"]:
            name = step.get("name", step.get("uses"))
            condition = step.get("if", "")
            with self.subTest(step=name):
                self.assertNotIn("needs.changes", condition)
                if step.get("name") == "Skip the shard":
                    self.assertEqual(condition, "env.SKIP == 'true'")
                    notices.append(name)
                else:
                    self.assertIn("env.SKIP != 'true'", condition)
        self.assertEqual(len(notices), 1)

    def test_the_notice_step_prints_a_notice_and_a_summary_line(self) -> None:
        step = next(s for s in self.job["steps"] if s.get("name") == "Skip the shard")
        self.assertIn("::notice::", step["run"])
        self.assertIn("GITHUB_STEP_SUMMARY", step["run"])


if __name__ == "__main__":
    unittest.main()
