# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every relative Markdown link in README.md and docs/*.md points at a file that exists, and its
#fragment at a heading of that file. Inline links, images, reference definitions and `[text][id]` uses are
checked, with titles and <angle> destinations. Path parts match case exactly, as on Linux. CHANGELOG.md (release-please) and docs/schemas.md (gen_api.py) are generated and not
checked. Links inside fenced blocks (backtick or tilde) and code spans are ignored."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {"schemas.md"}  # gen_api.py output; regex patterns in it look like links
DOCS = [ROOT / "README.md", *(p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in GENERATED)]
def destination(capture: bool) -> str:
    """The `(target "title")` part of an inline link or image: one grammar for every use. Only the outer one
    captures the target."""
    target = r"(<[^>]*>|[^)\s]+)" if capture else r"(?:<[^>]*>|[^)\s]+)"
    return r"""\(\s*""" + target + r"""(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)"""


LINK = re.compile(r"!?\[[^\]]*\]" + destination(True))
# A link whose label holds an image: `[![alt](img.png)](page.md)`. LINK and REFERENCE find the image itself.
IMAGE_LINK = re.compile(r"\[[^\[\]]*!\[[^\]]*\](?:" + destination(False) + r"|\[[^\]]*\])[^\[\]]*\]" + destination(True))
REFERENCE = re.compile(r"!?\[[^\]]+\]\[([^\]]+)\]")
DEFINITION = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)")
CODE_SPAN = re.compile(r"`[^`]*`")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def prose_lines(path: Path, strip_code: bool = True) -> list[str]:
    """The lines outside fenced code blocks. A fence closes on the same character, at least as long."""
    fence = ""
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        opened = FENCE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = ""
        elif opened:
            fence = opened.group(1)
        else:
            kept.append(CODE_SPAN.sub(" ", line) if strip_code else line)
    return kept


def unwrap(target: str) -> str:
    return target[1:-1] if target.startswith("<") and target.endswith(">") else target


def exists_exact(path: Path, root: Path) -> bool:
    """Whether `path` exists with the exact case of every part below `root`, as on Linux."""
    if not path.exists():
        return False
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    folder = root
    for part in parts:
        if part not in os.listdir(folder):
            return False
        folder = folder / part
    return True


def slug(heading: str) -> str:
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path: Path) -> set[str]:
    """The heading anchors of `path`. A repeated heading gets `-1`, `-2` and so on, as GitHub numbers it."""
    counts: dict[str, int] = {}
    found: set[str] = set()
    for line in prose_lines(path, strip_code=False):
        if match := re.match(r"#{1,6}\s+(.*)", line):
            base = slug(match.group(1))
            anchor = base
            while anchor in found:  # every emitted slug is reserved, a heading named `x-1` included
                counts[base] = counts.get(base, 0) + 1
                anchor = f"{base}-{counts[base]}"
            found.add(anchor)
    return found


def targets(doc: Path) -> list[str]:
    """Targets of the inline links and reference definitions of `doc`. A `[text][id]` use with no
    definition yields the target `[id]`, which no file matches, so `problems` reports it."""
    lines = prose_lines(doc)
    defined = {m.group(1).lower() for line in lines if (m := DEFINITION.match(line))}
    found = [unwrap(m.group(2)) for line in lines if (m := DEFINITION.match(line))]
    for line in lines:
        found.extend(unwrap(target) for target in LINK.findall(line))
        found.extend(unwrap(target) for target in IMAGE_LINK.findall(line))
        found.extend(f"[{ident}]" for ident in REFERENCE.findall(line) if ident.lower() not in defined)
    return found


def problems(doc: Path, root: Path) -> list[str]:
    found = []
    for target in targets(doc):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        name, _, fragment = target.partition("#")
        name, fragment = unquote(name), unquote(fragment)
        if "\0" in name or "\0" in fragment:
            found.append(f"{doc.relative_to(root)}: {target} is an invalid destination")
            continue
        path = doc if not name else (doc.parent / name).resolve()
        if not exists_exact(path, root.resolve()):
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

    def check(self, files: dict[str, str], text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name, body in files.items():
                (root / name).write_text(body, encoding="utf-8")
            doc = root / "doc.md"
            doc.write_text(text, encoding="utf-8")
            return sorted(problems(doc, root))

    def test_numbers_the_anchors_of_duplicate_headings_as_github_does(self) -> None:
        files = {"real.md": "# X\n\n## X\n\n### X\n"}
        text = "[a](real.md#x) [b](real.md#x-1) [c](real.md#x-2) [d](real.md#x-3)\n"
        self.assertEqual(self.check(files, text), ["doc.md: real.md#x-3 has no such heading"])

    def test_reports_a_missing_image(self) -> None:
        text = "![alt](gone.png) ![ok](real.png) [link](gone.md) ![ref][r]\n\n[r]: gone2.png\n"
        self.assertEqual(self.check({"real.png": "x\n"}, text), [
            "doc.md: gone.md does not exist", "doc.md: gone.png does not exist", "doc.md: gone2.png does not exist"])

    def test_decodes_percent_encoding_in_the_path_and_the_fragment(self) -> None:
        files = {"has space.md": "# My Heading\n", "real.md": "x\n"}
        text = ("[a](has%20space.md#my-heading) [b](has%20space.md#my%2Dheading) [c](<has space.md>) "
                "[d](gone%20too.md) [e](real.md#no%20such)\n")
        self.assertEqual(self.check(files, text), [
            "doc.md: gone%20too.md does not exist", "doc.md: real.md#no%20such has no such heading"])

    def test_checks_the_link_around_an_image_as_well_as_the_image(self) -> None:
        files = {"real.png": "x\n", "real.md": "x\n"}
        text = ("[![ok](real.png)](real.md) [![bad link](real.png)](gone.md) [![bad image](gone.png)](real.md)\n"
                "[![ref][i]](gone2.md)\n\n[i]: real.png\n")
        self.assertEqual(self.check(files, text), [
            "doc.md: gone.md does not exist", "doc.md: gone.png does not exist", "doc.md: gone2.md does not exist"])

    def test_checks_the_link_around_an_image_whose_title_holds_a_parenthesis(self) -> None:
        files = {"real.png": "x\n", "real.md": "x\n"}
        text = ('[![alt](real.png "a) [b]")](gone.md) [![alt](real.png "a) [b]")](real.md) '
                '[![alt](gone2.png "a) [b]")](real.md)\n')
        self.assertEqual(self.check(files, text), ["doc.md: gone.md does not exist", "doc.md: gone2.png does not exist"])

    def test_reports_an_invalid_destination_and_keeps_checking(self) -> None:
        text = "[bad](a%00b.md) [bad anchor](real.md#x%00y) [gone](gone.md)\n"
        self.assertEqual(self.check({"real.md": "# Top\n"}, text), [
            "doc.md: a%00b.md is an invalid destination",
            "doc.md: gone.md does not exist",
            "doc.md: real.md#x%00y is an invalid destination"])

    def test_reserves_every_slug_it_emits_when_headings_collide(self) -> None:
        files = {"real.md": "# X\n\n# X\n\n# X-1\n\n# X\n"}
        text = "[a](real.md#x) [b](real.md#x-1) [c](real.md#x-1-1) [d](real.md#x-2) [e](real.md#x-3)\n"
        self.assertEqual(self.check(files, text), ["doc.md: real.md#x-3 has no such heading"])

    def test_code_spans_separate_bracketed_phrases(self) -> None:
        text = "[first] `code` [second] and [a]`x`[b]\n"
        self.assertEqual(self.check({}, text), [])

    def test_reports_a_broken_link_that_has_a_title(self) -> None:
        text = '[t](gone.md "Title") [u](gone2.md \'Title\') [ok](real.md "Title")\n'
        self.assertEqual(self.check({"real.md": "x\n"}, text),
                         ["doc.md: gone.md does not exist", "doc.md: gone2.md does not exist"])

    def test_reads_an_angle_bracket_destination_without_its_brackets(self) -> None:
        text = "[a](<gone.md>) [b](<has space.md>) [c](<real.md#top>)\n\n[d]: <gone2.md>\n[e]: <has space.md>\n[use][d] [use][e]\n"
        files = {"real.md": "# Top\n", "has space.md": "x\n"}
        self.assertEqual(self.check(files, text), ["doc.md: gone.md does not exist", "doc.md: gone2.md does not exist"])

    def test_ignores_links_in_tilde_fences_and_matches_the_fence_marker(self) -> None:
        text = ("~~~\n[tilde](a.md)\n~~~\n"
                "~~~~\n```\n[inner](b.md)\n```\n~~~~\n"
                "```\n~~~\n[backtick](c.md)\n```\n"
                "[after](d.md)\n")
        self.assertEqual(self.check({}, text), ["doc.md: d.md does not exist"])

    def test_checks_the_case_of_every_path_part(self) -> None:
        text = "[a](Real.md) [b](sub/Inner.md) [c](Sub/inner.md) [ok](sub/inner.md) [d](real.md#Top)\n"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "sub").mkdir()
            (root / "sub" / "inner.md").write_text("# Top\n", encoding="utf-8")
            (root / "real.md").write_text("# Top\n", encoding="utf-8")
            doc = root / "doc.md"
            doc.write_text(text, encoding="utf-8")
            self.assertEqual(sorted(problems(doc, root)), [
                "doc.md: Real.md does not exist",
                "doc.md: Sub/inner.md does not exist",
                "doc.md: real.md#Top has no such heading",
                "doc.md: sub/Inner.md does not exist",
            ])


if __name__ == "__main__":
    unittest.main()
