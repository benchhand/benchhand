# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every relative Markdown link and image in README.md and docs/*.md points at a file that exists,
and its #fragment at a heading of that file. markdown-it-py (CommonMark) parses the files, so
inline links with any title form, <angle> destinations, reference links and definitions, and fence
rules follow the specification. Links inside fenced blocks and code spans are ignored. A
`[text][id]` use with no definition is reported as `[id]`. Path parts match case exactly, as on
Linux. CHANGELOG.md (release-please) and docs/schemas.md (gen_api.py) are generated and not
checked."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {"schemas.md"}  # gen_api.py output; regex patterns in it look like links
DOCS = [ROOT / "README.md", *(p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in GENERATED)]
UNDEFINED_REFERENCE = re.compile(r"\[[^\]]+\]\[([^\]]+)\]")
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def parse(path: Path) -> tuple[list[Token], dict[str, dict[str, str]]]:
    env: dict[str, dict[str, str]] = {}
    return MarkdownIt("commonmark").parse(path.read_text(encoding="utf-8"), env), env


def slug(heading: str) -> str:
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path: Path) -> set[str]:
    tokens, _ = parse(path)
    return {slug(after.content) for token, after in zip(tokens, tokens[1:]) if token.type == "heading_open"}


def targets(doc: Path) -> list[str]:
    """Targets of the links and images of `doc`, and of every reference definition. A `[text][id]`
    use with no definition yields the target `[id]`, which no file matches, so `problems` reports it."""
    tokens, env = parse(doc)
    found = [definition["href"] for definition in env.get("references", {}).values()]
    for token in tokens:
        if token.type != "inline":
            continue
        children = token.children or []
        found.extend(child.attrGet("href") or "" for child in children if child.type == "link_open")
        found.extend(child.attrGet("src") or "" for child in children if child.type == "image")
        text = "".join(child.content for child in children if child.type == "text")
        defined = {label.lower() for label in env.get("references", {})}
        found.extend(f"[{ident}]" for ident in UNDEFINED_REFERENCE.findall(text) if ident.lower() not in defined)
    return list(dict.fromkeys(unquote(target) for target in found))


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


def problems(doc: Path, root: Path) -> list[str]:
    found = []
    for target in targets(doc):
        if SCHEME.match(target):
            continue
        name, _, fragment = target.partition("#")
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

    def test_reports_a_broken_link_with_a_parenthesised_title(self) -> None:
        text = "[t](gone.md (Title)) [ok](real.md (Title))\n"
        self.assertEqual(self.check({"real.md": "x\n"}, text), ["doc.md: gone.md does not exist"])

    def test_a_tilde_fence_closer_followed_by_text_does_not_close_the_fence(self) -> None:
        text = "~~~\n[a](a.md)\n~~~ not a closer\n[b](b.md)\n~~~\n[after](d.md)\n"
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
