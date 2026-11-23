# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Decide whether a pull request can skip the skill test shards.

A shard is skipped only when every changed path is one that no skill test is known to read:
repo-root Markdown, ``docs/``, ``agents/*.md``, ``harness/`` except ``install.py``,
``skills/*/SKILL.md`` except cad-concept's and ``skills/*/references/*.md`` except cad-record's ``layout.md``.

Everything else runs every shard: any other path, a push, a missing SHA, a diff error, an empty
diff, and any symlink, submodule, or mode or type change. The diff is read with modes because a
name alone cannot tell a Markdown file from a symlink to code.

The safe set rests on ``test_docs_guard.py``, which scans the skill tests for literals that name
these paths. That scan is a name check and cannot see a path built from an imported constant.

``check`` writes the job output ``skip`` (``true`` or ``false``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# cad-flow's setup test needs this file to exist; cad-record's test_layout.py compares layout.md
# with layout.py's output; cad-concept's test_concept.py runs the launch command that its SKILL.md gives.
READ_BY_SKILL_TESTS = frozenset(
    {"harness/install.py", "skills/cad-record/references/layout.md", "skills/cad-concept/SKILL.md"}
)
# git's mode for a regular file; 000000 is the missing side of an added or deleted file
REGULAR = frozenset({"100644", "100755"})
ABSENT = "000000"
PLAIN_STATUSES = frozenset({"A", "M", "D"})


class DiffError(RuntimeError):
    """The changed files could not be determined."""


@dataclass(frozen=True)
class Change:
    path: str
    status: str = "M"
    old_mode: str = "100644"
    new_mode: str = "100644"

    def is_plain(self) -> bool:
        """A regular file added, deleted or edited in place: no symlink, submodule or mode change."""
        modes = (self.old_mode, self.new_mode)
        if self.status not in PLAIN_STATUSES or any(
            mode not in REGULAR | {ABSENT} for mode in modes
        ):
            return False
        return ABSENT in modes or self.old_mode == self.new_mode


def is_safe(path: str) -> bool:
    if not path or path in READ_BY_SKILL_TESTS:
        return False
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        return path.endswith(".md") or path == "CHANGELOG"
    if parts[0] in ("docs", "harness"):
        return True
    if parts[0] == "agents":
        return len(parts) == 2 and path.endswith(".md")
    if parts[0] == "skills" and len(parts) >= 3:
        if len(parts) == 3:
            return parts[2] == "SKILL.md"
        return len(parts) == 4 and parts[2] == "references" and path.endswith(".md")
    return False


def skips_skills(changes: Iterable[Change]) -> bool:
    """True when there is a change and every one is a plain edit of a safe path."""
    listed = list(changes)
    return bool(listed) and all(c.is_plain() and is_safe(c.path) for c in listed)


def parse_raw(output: str) -> list[Change]:
    """Read ``git diff --raw -z``: ``:<old mode> <new mode> <old id> <new id> <status>``, then the path."""
    fields = output.split("\0")
    changes = []
    for index in range(0, len(fields) - 1, 2):
        meta = fields[index].split()
        if len(meta) != 5 or not meta[0].startswith(":"):
            raise DiffError(f"unreadable diff record: {fields[index]!r}")
        changes.append(Change(fields[index + 1], meta[4][:1], meta[0][1:], meta[1]))
    return changes


def git_diff(root: Path, base: str, head: str) -> list[Change]:
    """Changes on ``head`` since its merge base with ``base``; a rename lists both paths."""
    done = subprocess.run(
        ["git", "diff", "--raw", "--no-renames", "-z", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if done.returncode:
        raise DiffError(done.stderr.strip() or f"git diff exited {done.returncode}")
    return parse_raw(done.stdout)


def decide(
    event: str, base: str, head: str, diff: Callable[[str, str], list[Change]]
) -> bool:
    """True to skip the skill shards; every doubt returns False."""
    if event != "pull_request" or not base or not head:
        return False
    try:
        return skips_skills(diff(base, head))
    except (DiffError, OSError):
        return False


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    skip = decide(
        os.environ.get("EVENT_NAME", ""),
        os.environ.get("BASE_SHA", ""),
        os.environ.get("HEAD_SHA", ""),
        lambda base, head: git_diff(root, base, head),
    )
    line = f"skip={'true' if skip else 'false'}"
    print(line)
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as out:
            out.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
