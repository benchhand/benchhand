#!/usr/bin/env python3
"""Parallel part builds in the kernel runner: CAD_MODEL_BUILD_JOBS, request order, per-part isolation.

Run: python3 -B -m unittest discover -s skills/cad-model/tests -p test_runner_parallel.py
`build_jobs` needs nothing; the build tests start the real CadQuery runner through `run_kernels` (uv).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from cadmodel import build, runner  # noqa: E402 - stdlib-only imports; OCP loads in the runner

BOX = """import time
import cadquery as cq
KERNEL = "cadquery"
PARAMS = {{"size": {size}}}
def build(params):
    time.sleep({delay})
    s = params["size"]
    return cq.Solid.makeBox(s, s + 1, s + 2)
"""
TWIN = """import cadquery as cq
KERNEL = "cadquery"
PARAMS = {}
def build(params):
    return cq.Solid.makeBox(3, 4, 5).moved(cq.Location(cq.Vector(5, 0, 0)))
"""
BROKEN = """KERNEL = "cadquery"
PARAMS = {}
def build(params):
    raise ValueError("no good")
"""
NOISY = """import logging
import cadquery as cq
KERNEL = "cadquery"
PARAMS = {}
def build(params):
    logging.getLogger("cadmodel.fillets").warning("skipped edge E1")
    return cq.Solid.makeBox(3, 4, 5)
"""
WHERE = """import json, os
import cadquery as cq
KERNEL = "cadquery"
PARAMS = {}
def build(params):
    with open(os.environ["WHERE_LOG"] + "/" + str(os.getpid()), "w") as out:
        json.dump({"pid": os.getpid(), "ppid": os.getppid(), "pgrp": os.getpgrp(), "runner_pgrp": os.getpgid(os.getppid())}, out)
    return cq.Solid.makeBox(3, 4, 5)
"""
# (pn, source): the slow part is first so a completion-order result would differ from request order
PROJECT = [
    ("TST-0001", BOX.format(size=10, delay=2.0)),
    ("TST-0002", BROKEN),
    ("TST-0003", NOISY),
    ("TST-0004", BOX.format(size=20, delay=0)),
    ("TST-0005", BOX.format(size=30, delay=0)),
]


class BuildJobs(unittest.TestCase):
    def test_default_is_cpus_parts_and_cap(self) -> None:
        for cpus, parts, want in [
            (16, 20, 8),
            (16, 3, 3),
            (2, 20, 2),
            (4, 1, 1),
            (None, 5, min(os.cpu_count() or 1, 5, 8)),
        ]:
            with self.subTest(cpus=cpus, parts=parts):
                self.assertEqual(runner.build_jobs(parts, {}, cpus), want)

    def test_env_overrides_but_never_exceeds_the_parts(self) -> None:
        for raw, parts, want in [("1", 5, 1), ("3", 5, 3), ("12", 5, 5), (" 2 ", 5, 2)]:
            with self.subTest(raw=raw):
                self.assertEqual(
                    runner.build_jobs(parts, {runner.BUILD_JOBS_ENV: raw}, 16), want
                )

    def test_env_must_be_a_positive_integer(self) -> None:
        for raw in ("0", "-1", "two", "1.5"):
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(ValueError, runner.BUILD_JOBS_ENV),
            ):
                runner.build_jobs(5, {runner.BUILD_JOBS_ENV: raw}, 16)


@unittest.skipUnless(shutil.which("uv"), "uv is required to start the runners")
class ParallelBuilds(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.where = cls.root / "pids"
        cls.where.mkdir()
        cls.requests = cls.request("main", PROJECT)
        cls.serial = cls.run_with("1", "serial")
        cls.pool = cls.run_with("3", "pool")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    @classmethod
    def request(
        cls, tag: str, project: list[tuple[str, str]], out: str = ""
    ) -> list[dict[str, str]]:
        parts = []
        for pn, source in project:
            folder = cls.root / tag / "hw" / "parts" / pn
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "model.py").write_text(source, encoding="utf-8")
            parts.append({"pn": pn, "name": pn, "model": str(folder / "model.py")})
        return parts

    @classmethod
    def run_with(
        cls, jobs: str, label: str, parts: list[dict[str, str]] | None = None
    ) -> dict:
        parts = [
            dict(p, step=str(cls.root / label / f"{p['pn']}.step"))
            for p in parts or cls.requests
        ]
        with mock.patch.dict(
            os.environ, {runner.BUILD_JOBS_ENV: jobs, "WHERE_LOG": str(cls.where)}
        ):
            return build.run_kernels({"cadquery": parts})["cadquery"]

    def test_results_follow_request_order_not_completion_order(self) -> None:
        self.assertEqual(list(self.pool["parts"]), [pn for pn, _ in PROJECT])
        reversed_parts = self.run_with("3", "reversed", list(reversed(self.requests)))
        self.assertEqual(
            list(reversed_parts["parts"]), [pn for pn, _ in reversed(PROJECT)]
        )

    def test_a_failing_part_fails_alone_with_the_serial_error(self) -> None:
        for result in (self.serial, self.pool):
            parts = result["parts"]
            self.assertEqual(
                [pn for pn, part in parts.items() if not part["ok"]], ["TST-0002"]
            )
        self.assertEqual(
            self.pool["parts"]["TST-0002"], self.serial["parts"]["TST-0002"]
        )
        self.assertEqual(
            self.pool["parts"]["TST-0002"]["error"],
            {
                "type": "ValueError",
                "message": "no good",
                "where": "model.py:4 in build",
            },
        )

    def test_fillet_warnings_stay_with_their_part(self) -> None:
        warned = {pn: part.get("warnings") for pn, part in self.pool["parts"].items()}
        self.assertEqual(
            warned,
            {
                "TST-0001": None,
                "TST-0002": None,
                "TST-0003": ["skipped edge E1"],
                "TST-0004": None,
                "TST-0005": None,
            },
        )

    def test_one_job_and_several_give_the_same_results_and_step_bytes(self) -> None:
        def stable(result: dict) -> dict:
            parts = {
                pn: {k: v for k, v in part.items() if k != "build_s"}
                for pn, part in result["parts"].items()
            }
            return {
                "parts": parts,
                "sources": result["sources"],
                "tools": result["tools"],
            }

        def step(label: str, pn: str) -> bytes:
            # OCCT stamps FILE_NAME with the wall clock: the only bytes allowed to differ
            data = (self.root / label / f"{pn}.step").read_bytes()
            return re.sub(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", b"<time>", data)

        self.assertEqual(stable(self.pool), stable(self.serial))
        for pn, part in self.serial["parts"].items():
            if part["ok"]:
                self.assertEqual(step("pool", pn), step("serial", pn), pn)

    def test_workers_share_the_runners_process_group_and_leave_no_bytecode(
        self,
    ) -> None:
        parts = self.request("located", [(f"TST-{i:04d}", WHERE) for i in range(1, 5)])
        for old in self.where.iterdir():
            old.unlink()
        result = self.run_with("2", "where", parts)
        self.assertTrue(
            all(part["ok"] for part in result["parts"].values()), result["parts"]
        )
        seen = [json.loads(path.read_text()) for path in self.where.iterdir()]
        self.assertTrue(seen)
        for entry in seen:  # the group build._stop kills is the runner's
            self.assertEqual(entry["pgrp"], entry["runner_pgrp"], entry)
            self.assertNotEqual(
                entry["pgrp"], os.getpgrp(), "run_kernels starts a session of its own"
            )
            self.assertNotEqual(entry["pid"], entry["ppid"])
        self.assertFalse(
            list(self.root.rglob("__pycache__")),
            "workers must not write bytecode beside model.py",
        )

    def test_step_occurrence_ids_do_not_depend_on_which_worker_built_what(self) -> None:
        # OCCT numbers NEXT_ASSEMBLY_USAGE_OCCURRENCE per process: every part must start at '1'
        parts = self.request("twins", [(f"TST-{i:04d}", TWIN) for i in range(1, 9)])
        result = self.run_with("2", "twins", parts)
        for pn in result["parts"]:
            text = (self.root / "twins" / f"{pn}.step").read_text()
            self.assertIn("NEXT_ASSEMBLY_USAGE_OCCURRENCE('1'", text, pn)
            self.assertNotRegex(text, r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('[2-9]'", pn)

    def test_a_bad_jobs_value_is_a_bad_request(self) -> None:
        with self.assertRaisesRegex(build.UsageError, "CAD_MODEL_BUILD_JOBS"):
            self.run_with("zero", "bad")


if __name__ == "__main__":
    unittest.main()
