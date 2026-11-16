import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_shards  # noqa: E402

PLAN = """
motion   skills/m/tests/test_m.py
motion-b skills/m/tests/test_m.py Slow Fast.test_two
record   skills/r/tests/test_r.py
"""
TRACKED = [
    "skills/a/tests/test_a.py",
    "skills/m/tests/test_m.py",
    "skills/r/tests/test_r.py",
]
NAMES = ["Fast.test_one", "Fast.test_two", "Slow.test_a", "Slow.test_b", "Other.test_c"]


class PlanTests(unittest.TestCase):
    def test_each_shard_runs_its_files_and_rest_runs_every_unnamed_file(self) -> None:
        entries = ci_shards.parse(PLAN)
        self.assertEqual(
            ci_shards.files(entries, "motion", TRACKED), ["skills/m/tests/test_m.py"]
        )
        self.assertEqual(
            ci_shards.files(entries, "motion-b", TRACKED), ["skills/m/tests/test_m.py"]
        )
        self.assertEqual(
            ci_shards.files(entries, "rest", TRACKED), ["skills/a/tests/test_a.py"]
        )
        self.assertEqual(
            ci_shards.files(entries, "rest", TRACKED, job=(3, 4)),
            ["skills/a/tests/test_a.py"],
        )
        self.assertEqual(
            ci_shards.order(entries), ["motion", "motion-b", "record", "rest"]
        )

    def test_a_matrix_that_could_skip_or_repeat_a_shard_is_refused(self) -> None:
        entries = ci_shards.parse(PLAN)
        refused = {
            "unknown shard": ("nope", None),
            "too few jobs": ("motion", (0, 3)),
            "too many jobs": ("motion", (0, 5)),
            # five jobs naming known shards, motion twice and motion-b never
            "a repeated shard": ("motion", (1, 4)),
            "a shard out of order": ("record", (1, 4)),
        }
        for case, (shard, job) in refused.items():
            with self.subTest(case=case), self.assertRaises(ci_shards.PlanError):
                ci_shards.files(entries, shard, TRACKED, job)

    def test_selected_tests_go_to_their_shard_and_the_owner_keeps_the_rest(
        self,
    ) -> None:
        shards = ci_shards.assign(
            ci_shards.parse(PLAN), "skills/m/tests/test_m.py", NAMES
        )
        self.assertEqual(
            shards,
            {
                "Fast.test_one": "motion",
                "Fast.test_two": "motion-b",
                "Slow.test_a": "motion-b",
                "Slow.test_b": "motion-b",
                "Other.test_c": "motion",
            },
        )
        self.assertEqual(
            set(ci_shards.assign([], "skills/a/tests/test_a.py", NAMES).values()),
            {"rest"},
        )

    def test_a_class_prefix_is_not_a_selector_of_a_longer_class_name(self) -> None:
        entries = ci_shards.parse("a f.py\nb f.py Slow\n")
        self.assertEqual(
            ci_shards.assign(entries, "f.py", ["Slow.test_a", "SlowToo.test_b"]),
            {"Slow.test_a": "b", "SlowToo.test_b": "a"},
        )

    def test_a_plan_that_could_drop_or_double_a_test_is_refused(self) -> None:
        refused = {
            "no owner": ("b f.py Slow\n", None),
            "two owners": ("a f.py\nb f.py\n", None),
            "untracked": ("a gone.py\n", "files"),
            "unused selector": ("a f.py\nb f.py Missing\n", "assign"),
            "double selection": ("a f.py\nb f.py Slow\nc f.py Slow.test_a\n", "assign"),
            "no file": ("lonely\n", None),
        }
        for case, (text, stage) in refused.items():
            with self.subTest(case=case), self.assertRaises(ci_shards.PlanError):
                entries = ci_shards.parse(text)
                if stage == "files":
                    ci_shards.files(entries, "a", ["f.py"])
                if stage == "assign":
                    ci_shards.assign(entries, "f.py", ["Slow.test_a", "Fast.test_b"])


class RunTests(unittest.TestCase):
    def test_a_shard_runs_only_its_tests_with_class_fixtures_and_fails_on_a_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test_probe.py"
            test_file.write_text(
                textwrap.dedent("""
                import unittest
                class Fast(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        cls.ready = True
                    def test_one(self):
                        self.assertTrue(self.ready)
                    def test_two(self):
                        self.fail("selected away")
                class Slow(unittest.TestCase):
                    def test_a(self):
                        self.fail("slow failure")
            """),
                encoding="utf-8",
            )

            def shard(name: str) -> subprocess.CompletedProcess[str]:
                plan = f"one {test_file}\ntwo {test_file} Fast.test_two Slow\n"
                return subprocess.run(
                    [
                        sys.executable,
                        str(Path(ci_shards.__file__)),
                        "run",
                        name,
                        str(test_file),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**os.environ, "SHARDS": plan},
                )

            one, two = shard("one"), shard("two")
            self.assertEqual(one.returncode, 0, one.stderr)
            self.assertIn("Ran 1 test", one.stderr)
            self.assertEqual(two.returncode, 1)
            self.assertIn("Ran 2 tests", two.stderr)
            self.assertIn("selected away", two.stderr)
            self.assertIn("slow failure", two.stderr)
            self.assertEqual(shard("three").returncode, 1)  # selects no test

    def test_an_import_error_fails_every_shard_of_its_file_with_its_own_traceback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test_broken.py"
            test_file.write_text("import not_a_module_anywhere\n", encoding="utf-8")
            # a split file: the selector would match no test, which must not hide the import error
            plan = f"one {test_file}\ntwo {test_file} Slow\n"
            for shard in ("one", "two"):
                with self.subTest(shard=shard):
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(Path(ci_shards.__file__)),
                            "run",
                            shard,
                            str(test_file),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        env={**os.environ, "SHARDS": plan},
                    )
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn("not_a_module_anywhere", proc.stderr)
                    self.assertNotIn("selectors match no test", proc.stderr)


if __name__ == "__main__":
    unittest.main()
