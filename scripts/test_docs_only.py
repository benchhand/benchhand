import ast
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docs_only  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class ClassifierTests(unittest.TestCase):
    def test_every_safe_class_is_safe(self) -> None:
        safe = [
            "README.md",
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
        self.assertTrue(docs_only.skips_skills(["README.md", "skills/cad-flow/SKILL.md"]))

    def test_one_unsafe_path_among_safe_ones_runs_everything(self) -> None:
        self.assertFalse(
            docs_only.skips_skills(["README.md", "skills/cad-flow/scripts/run.py"])
        )

    def test_an_empty_diff_runs_everything(self) -> None:
        self.assertFalse(docs_only.skips_skills([]))


class DecideTests(unittest.TestCase):
    def test_a_pull_request_of_safe_paths_skips(self) -> None:
        self.assertTrue(
            docs_only.decide("pull_request", "b", "h", lambda b, h: ["docs/a.md"])
        )

    def test_a_pull_request_with_a_code_path_runs_everything(self) -> None:
        self.assertFalse(
            docs_only.decide(
                "pull_request", "b", "h", lambda b, h: ["docs/a.md", "scripts/x.py"]
            )
        )

    def test_a_push_runs_everything_without_reading_a_diff(self) -> None:
        def diff(base: str, head: str) -> list[str]:
            raise AssertionError("a push needs no diff")

        self.assertFalse(docs_only.decide("push", "b", "h", diff))

    def test_a_missing_sha_runs_everything(self) -> None:
        for base, head in (("", "h"), ("b", ""), ("", "")):
            with self.subTest(base=base, head=head):
                self.assertFalse(
                    docs_only.decide(
                        "pull_request", base, head, lambda b, h: ["docs/a.md"]
                    )
                )

    def test_a_diff_error_runs_everything(self) -> None:
        def diff(base: str, head: str) -> list[str]:
            raise docs_only.DiffError("no merge base")

        self.assertFalse(docs_only.decide("pull_request", "b", "h", diff))


class GitDiffTests(unittest.TestCase):
    def test_a_rename_lists_both_paths_so_leaving_a_guarded_path_is_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run("git", "init", "-q", "-b", "main", cwd=repo)
            run("git", "config", "user.email", "t@example.com", cwd=repo)
            run("git", "config", "user.name", "t", cwd=repo)
            (repo / "harness").mkdir()
            (repo / "harness" / "install.py").write_text("print('x')\n" * 20)
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-qm", "base", cwd=repo)
            run("git", "checkout", "-qb", "topic", cwd=repo)
            run("git", "mv", "harness/install.py", "harness/installer.py", cwd=repo)
            run("git", "commit", "-qam", "rename", cwd=repo)
            changed = docs_only.git_diff(repo, "main", "topic")
            self.assertEqual(
                sorted(changed), ["harness/install.py", "harness/installer.py"]
            )
            self.assertFalse(docs_only.skips_skills(changed))

    def test_a_bad_revision_is_a_diff_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run("git", "init", "-q", cwd=Path(tmp))
            with self.assertRaises(docs_only.DiffError):
                docs_only.git_diff(Path(tmp), "0" * 40, "1" * 40)


# A skill test may name a safe-set path only where it writes the file in a temp directory or
# does not read the repo's copy. Each entry below was read by hand. A new test file that names
# a safe-set path fails until someone reads it and either lists it here or narrows is_safe.
ALLOWED = {
    # writes stub SKILL.md and agent files into a temp project to build installer receipts
    "skills/cad-flow/tests/test_preflight.py": {"SKILL.md", "agents"},
    # "docs" below is the hardware project's own docs folder (hw/docs/...), built in a temp dir
    "skills/cad-record/tests/test_build_guide.py": {"docs"},
    "skills/cad-record/tests/test_evidence.py": {"docs"},
    "skills/cad-record/tests/test_migrate.py": {"docs"},
    "skills/cad-record/tests/test_migration_plan.py": {"docs"},
    "skills/cad-record/tests/test_release.py": {"docs"},
    "skills/cad-record/tests/test_schema_manufacturing.py": {"docs"},
    "skills/cad-record/tests/test_schema_registry.py": {"docs"},
    # asserts the plan names harness/install.py; the installer is mocked, and setup.py:170 checks
    # that the file exists, which is why harness/install.py is outside the safe set
    "skills/cad-flow/tests/test_setup.py": {"harness"},
    # compares references/layout.md with layout.py's output; layout.md is outside the safe set
    # (its "docs" is again hw/docs in a temp project)
    "skills/cad-record/tests/test_layout.py": {"references", "docs"},
}
# A string literal that is a safe-set file name or a directory part of a safe-set path.
MENTION = re.compile(
    r"SKILL\.md|README|CHANGELOG|(?:^|/)(?P<part>references|agents|docs|harness)(?:/|$)"
)


def mentions(source: str) -> set[str]:
    """The safe-set names found in the string literals of a test file, docstrings excluded."""
    tree = ast.parse(source)
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            for match in MENTION.finditer(node.value):
                found.add(match.group("part") or match.group())
    return found


class GuardTests(unittest.TestCase):
    def test_a_test_that_reads_a_skill_md_is_caught(self) -> None:
        source = 'from pathlib import Path\nTEXT = Path("skills/x/SKILL.md").read_text()\n'
        self.assertEqual(mentions(source), {"SKILL.md"})

    def test_a_joined_path_part_is_caught(self) -> None:
        source = 'T = (SKILL / "references" / "layout.md").read_text()\n'
        self.assertEqual(mentions(source), {"references"})

    def test_a_path_inside_one_string_is_caught(self) -> None:
        self.assertEqual(mentions('p = "skills/x/references/y.md"\n'), {"references"})

    def test_prose_a_comment_and_an_unrelated_name_are_not_mentions(self) -> None:
        source = '"""Reads SKILL.md."""\n# see docs/\nx = "hw/decisions/DR-001.md"\ny = "mydocs"\n'
        self.assertEqual(mentions(source), set())

    def test_no_skill_test_names_a_safe_set_path_outside_the_allowlist(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "skills/*/tests/*.py", "skills/*/test_*.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        self.assertTrue(tracked)
        unlisted = {}
        for path in tracked:
            extra = mentions((ROOT / path).read_text(encoding="utf-8")) - ALLOWED.get(
                path, set()
            )
            if extra:
                unlisted[path] = sorted(extra)
        self.assertEqual(
            unlisted,
            {},
            "a skill test names a path the docs-only rule treats as safe: read it, then"
            " list it in ALLOWED or take the path out of docs_only.is_safe",
        )

    def test_every_allowlisted_name_is_still_used(self) -> None:
        for path, names in ALLOWED.items():
            with self.subTest(path=path):
                found = mentions((ROOT / path).read_text(encoding="utf-8"))
                self.assertTrue(names <= found, f"{path} no longer names {names - found}")


if __name__ == "__main__":
    unittest.main()
