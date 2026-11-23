# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every relative Markdown link in README.md and docs/*.md points at a file that exists, and its
#fragment at a heading of that file. Inline links, reference definitions and `[text][id]` uses are
checked. CHANGELOG.md (release-please) and docs/schemas.md (gen_api.py) are generated and not
checked. Links inside code blocks and code spans are ignored."""

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {"schemas.md"}  # gen_api.py output; regex patterns in it look like links
DOCS = [ROOT / "README.md", *(p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in GENERATED)]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
REFERENCE = re.compile(r"(?<!!)\[[^\]]+\]\[([^\]]+)\]")
DEFINITION = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)")
CODE_SPAN = re.compile(r"`[^`]*`")
FENCE = re.compile(r"^\s*```")


def prose_lines(path: Path, strip_code: bool = True) -> list[str]:
    inside = False
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            inside = not inside
        elif not inside:
            kept.append(CODE_SPAN.sub("", line) if strip_code else line)
    return kept


def slug(heading: str) -> str:
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for line in prose_lines(path, strip_code=False) if (m := re.match(r"#{1,6}\s+(.*)", line))}


def targets(doc: Path) -> list[str]:
    """Targets of the inline links and reference definitions of `doc`. A `[text][id]` use with no
    definition yields the target `[id]`, which no file matches, so `problems` reports it."""
    lines = prose_lines(doc)
    defined = {m.group(1).lower() for line in lines if (m := DEFINITION.match(line))}
    found = [m.group(2) for line in lines if (m := DEFINITION.match(line))]
    for line in lines:
        found.extend(LINK.findall(line))
        found.extend(f"[{ident}]" for ident in REFERENCE.findall(line) if ident.lower() not in defined)
    return found


def problems(doc: Path, root: Path) -> list[str]:
    found = []
    for target in targets(doc):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        name, _, fragment = target.partition("#")
        path = doc if not name else (doc.parent / name).resolve()
        if not path.exists():
            found.append(f"{doc.relative_to(root)}: {target} does not exist")
        elif fragment and path.suffix == ".md" and fragment not in anchors(path):
            found.append(f"{doc.relative_to(root)}: {target} has no such heading")
    return found


class MarkdownLinkTests(unittest.TestCase):
    def test_relative_links_resolve(self) -> None:
        self.assertEqual([found for doc in DOCS for found in problems(doc, ROOT)], [])

    def test_reports_broken_links_of_every_form(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "real.md").write_text("# Real heading\n", encoding="utf-8")
            doc = root / "doc.md"
            doc.write_text(
                "[ok](real.md#real-heading) [bad inline](gone.md) [bad anchor](real.md#nope)\n"
                "[ref ok][a] [ref gone][b] [ref undefined][c] [url](https://example.org/x) `[code](nope.md) [x][y]`\n"
                "\n[a]: real.md\n[b]: missing.md\n"
                "```\n[fenced](ignored.md)\n```\n",
                encoding="utf-8")
            self.assertEqual(sorted(problems(doc, root)), [
                "doc.md: [c] does not exist",
                "doc.md: gone.md does not exist",
                "doc.md: missing.md does not exist",
                "doc.md: real.md#nope has no such heading",
            ])


if __name__ == "__main__":
    unittest.main()
