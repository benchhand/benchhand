"""Split the skill test files into CI shards and run one shard's share of a file.

The plan is the SHARDS text, one entry per line: ``<shard> <test file> [<selector> ...]``.
A selector is a test class (``StepTests``) or one test (``StepTests.test_x``) of that file.

- A file with no entry runs whole in the ``rest`` shard.
- A file's entry without selectors owns every test of the file that no other entry selects.
- A file that has entries with selectors needs exactly one entry without them, so no test can
  fall between shards. A selector that matches no test, or a test two selectors match, is an error.

``files <shard>`` prints the shard's test files; ``run <shard> <file>`` runs the shard's tests of
one file with unittest and prints the slowest ones.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REST = "rest"


class PlanError(ValueError):
    """The SHARDS text cannot place every test in exactly one shard."""


@dataclass(frozen=True)
class Entry:
    shard: str
    path: str
    selectors: tuple[str, ...]


def parse(text: str) -> list[Entry]:
    entries = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < 2:
            raise PlanError(f"a shard entry needs a shard and a test file: {line!r}")
        entries.append(Entry(fields[0], fields[1], tuple(fields[2:])))
    owners: dict[str, list[Entry]] = {}
    for entry in entries:
        owners.setdefault(entry.path, []).append(entry)
    for path, listed in owners.items():
        whole = [entry for entry in listed if not entry.selectors]
        if len(whole) != 1:
            raise PlanError(
                f"{path} needs exactly one entry without selectors, has {len(whole)}"
            )
    return entries


def order(entries: list[Entry]) -> list[str]:
    """The plan's shards in first-mention order, ``rest`` last: the order the CI matrix must list."""
    shards = list(
        dict.fromkeys(entry.shard for entry in entries if entry.shard != REST)
    )
    return [*shards, REST]


def files(
    entries: list[Entry],
    shard: str,
    tracked: Iterable[str],
    job: tuple[int, int] | None = None,
) -> list[str]:
    """The test files of ``shard``. ``job`` is (index, total) of this CI job in its matrix."""
    tracked = list(tracked)
    shards = order(entries)
    if shard not in shards:
        raise PlanError(f"shard {shard} is not in SHARDS")
    # job indexes are unique, so a matrix whose job i runs the plan's shard i runs each shard once
    if job is not None:
        index, total = job
        if total != len(shards) or shards[index] != shard:
            raise PlanError(
                f"the matrix must list the shards [{', '.join(shards)}] in that order;"
                f" job {index} of {total} runs {shard}"
            )
    named = {entry.path for entry in entries}
    missing = sorted(named - set(tracked))
    if missing:
        raise PlanError(f"in SHARDS but not a tracked test file: {', '.join(missing)}")
    chosen = {entry.path for entry in entries if entry.shard == shard}
    if shard == REST:
        chosen |= set(tracked) - named
    return [path for path in tracked if path in chosen]


def assign(entries: list[Entry], path: str, names: Iterable[str]) -> dict[str, str]:
    """Each test name (``Class.test``) of a file -> its shard."""
    listed = [entry for entry in entries if entry.path == path]
    if not listed:
        return {name: REST for name in names}
    owner = next(entry.shard for entry in listed if not entry.selectors)
    selections = [
        (entry.shard, selector) for entry in listed for selector in entry.selectors
    ]
    used = {selection: 0 for selection in selections}
    shards = {}
    for name in names:
        hits = [
            (shard, selector)
            for shard, selector in selections
            if name == selector or name.startswith(selector + ".")
        ]
        if len(hits) > 1:
            raise PlanError(
                f"{path}: {name} is selected by {', '.join(selector for _, selector in hits)}"
            )
        for hit in hits:
            used[hit] += 1
        shards[name] = hits[0][0] if hits else owner
    unused = [selector for (_, selector), count in used.items() if not count]
    if unused:
        raise PlanError(f"{path}: selectors match no test: {', '.join(unused)}")
    return shards


def flatten(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def run(entries: list[Entry], shard: str, path: str) -> int:
    test_file = Path(path)
    module = test_file.stem
    tests = list(
        flatten(
            unittest.TestLoader().discover(
                str(test_file.parent), pattern=test_file.name
            )
        )
    )
    prefix = module + "."
    # an import or load failure is a test of unittest's own; it names no test of the file, so
    # run it alone in every shard of the file, before any selector can fail on the missing names
    broken = [test for test in tests if not test.id().startswith(prefix)]
    if broken:
        unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(broken))
        return 1
    names = [test.id().removeprefix(prefix) for test in tests]
    shards = assign(entries, path, names)
    chosen = [test for test in tests if shards[test.id().removeprefix(prefix)] == shard]
    if not chosen:
        print(f"{path}: shard {shard} selects no test", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=1, durations=10).run(
        unittest.TestSuite(chosen)
    )
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str]) -> int:
    entries = parse(os.environ.get("SHARDS", ""))
    if argv[:1] == ["files"] and len(argv) in (2, 4):
        tracked = subprocess.run(
            ["git", "ls-files", "skills/*/test_*.py", "skills/*/tests/test_*.py"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        job = (int(argv[2]), int(argv[3])) if len(argv) == 4 else None
        print("\n".join(files(entries, argv[1], tracked, job)))
        return 0
    if argv[:1] == ["run"] and len(argv) == 3:
        return run(entries, argv[1], argv[2])
    print(
        "usage: ci_shards.py files <shard> [<job index> <job total>] | run <shard> <test file>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except PlanError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from None
