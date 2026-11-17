"""Shared runner of the semantic-output-oracle families: one case is built and checked by
`_oracle_driver.py` under `uv run --script`. Its own environment holds cad-model's runtime, so the
corpus process needs nothing beyond what rates.py declares.

`case(model=..., fault=...)` returns the `run` function of a Pair: run(True) builds the project with the
model overrides and the one exporter fault, run(False) the unmodified project. Verdicts are cached per
process, so the clean twin that several pairs share is built once."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any

from corpus import FAMILIES_DIR, Outcome, Skip

DRIVER = FAMILIES_DIR / "_oracle_driver.py"
_CACHE: dict[str, Outcome] = {}


def verdict(model: dict[str, Any] | None, fault: dict[str, Any] | None) -> Outcome:
    key = json.dumps({"model": model, "fault": fault}, sort_keys=True)
    if key not in _CACHE:
        uv = shutil.which("uv")
        if uv is None:
            raise Skip("uv is not on PATH: the cad-model runtime cannot be created")
        with tempfile.TemporaryDirectory(prefix="mfg-oracle-") as work:
            case_file = f"{work}/case.json"
            with open(case_file, "w", encoding="utf-8") as handle:
                handle.write(key)
            proc = subprocess.run([uv, "run", "--quiet", "--script", str(DRIVER), case_file, work],
                                  capture_output=True, text=True, check=False)  # no timeout: cold imports take minutes
        if proc.returncode != 0:
            raise RuntimeError(f"driver exited {proc.returncode}: {proc.stderr[-800:]}")
        _CACHE[key] = json.loads(proc.stdout.strip().splitlines()[-1])["outcome"]
    return _CACHE[key]


def case(model: dict[str, Any] | None = None, fault: dict[str, Any] | None = None) -> Callable[[bool], Outcome]:
    def run(mutant: bool) -> Outcome:
        return verdict(model, fault) if mutant else verdict(None, None)
    return run
