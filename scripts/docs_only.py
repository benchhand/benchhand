"""Decide whether a pull request can skip the skill test shards.

A shard is skipped only when every changed path is one that no skill test reads: repo-root
Markdown, ``docs/``, ``agents/*.md``, ``harness/`` except ``install.py``, ``skills/*/SKILL.md`` and
``skills/*/references/*.md`` except cad-record's ``layout.md``. Anything else, a push, a missing
SHA, a diff error or an empty diff runs every shard.
``test_docs_only.py`` fails when a skill test starts to name one of these paths.

``check`` writes the job output ``skip`` (``true`` or ``false``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

# cad-flow's setup test needs this file to exist; cad-record's test_layout.py compares layout.md
# with layout.py's output.
READ_BY_SKILL_TESTS = frozenset(
    {"harness/install.py", "skills/cad-record/references/layout.md"}
)


class DiffError(RuntimeError):
    """The changed files could not be determined."""


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


def skips_skills(changed: Iterable[str]) -> bool:
    """True when there is a change and every path in it is safe; an empty diff runs everything."""
    paths = list(changed)
    return bool(paths) and all(is_safe(path) for path in paths)


def git_diff(root: Path, base: str, head: str) -> list[str]:
    """Files changed on ``head`` since its merge base with ``base``; a rename lists both paths."""
    done = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if done.returncode:
        raise DiffError(done.stderr.strip() or f"git diff exited {done.returncode}")
    return [path for path in done.stdout.split("\0") if path]


def decide(
    event: str, base: str, head: str, diff: Callable[[str, str], list[str]]
) -> bool:
    """True to skip the skill shards; every doubt returns False."""
    if event != "pull_request" or not base or not head:
        return False
    try:
        return skips_skills(diff(base, head))
    except DiffError:
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
