# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every relative Markdown link in README.md and docs/*.md points at a file that exists, and its
#fragment at a heading of that file. CHANGELOG.md (release-please) and docs/schemas.md (gen_api.py) are generated and not checked.
Links inside fenced code blocks are ignored."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {"schemas.md"}  # gen_api.py output; regex patterns in it look like links
DOCS = [ROOT / "README.md", *(p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in GENERATED)]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^\s*```")


def prose_lines(path: Path) -> list[str]:
    inside = False
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            inside = not inside
        elif not inside:
            kept.append(line)
    return kept


def slug(heading: str) -> str:
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for line in prose_lines(path) if (m := re.match(r"#{1,6}\s+(.*)", line))}


class MarkdownLinkTests(unittest.TestCase):
    def test_relative_links_resolve(self) -> None:
        problems = []
        for doc in DOCS:
            for line in prose_lines(doc):
                for target in LINK.findall(line):
                    if re.match(r"[a-z][a-z0-9+.-]*:", target):
                        continue
                    name, _, fragment = target.partition("#")
                    path = doc if not name else (doc.parent / name).resolve()
                    if not path.exists():
                        problems.append(f"{doc.relative_to(ROOT)}: {target} does not exist")
                    elif fragment and path.suffix == ".md" and fragment not in anchors(path):
                        problems.append(f"{doc.relative_to(ROOT)}: {target} has no such heading")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
