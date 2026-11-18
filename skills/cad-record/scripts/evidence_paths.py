"""Where the files an accepted ledger entry cites may live.

Evidence in the record must be reproducible from the record: a file inside the project root that is
either tracked by git or lives in a record-managed result location (a sealed hw/versions/<V>/, or a
version-bound bundle hw/<kind>/<V>/<S>/ such as hw/checks/<V>/<S>/evidence/). The rule refuses scratch (`.cad-work/`,
`hw/.cad/`), temp directories, the home directory and anything outside the root.

Structured path fields (artifacts, bundle, source.artifact) and path-like tokens in the free-text fields of
the evidence and findings ledgers go through the same rule. A token is path-like only with a path shape: a
scratch prefix, an absolute path, a `..` segment, or a file extension after a `/`.

Standard library only. evidence.py loads it by path.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCRATCH_PREFIXES = (".cad-work/", "/tmp/", "/var/folders/", "~/")
PROMOTE = ("promote the file into the record (for example under hw/checks/<V>/<S>/evidence/) and list it in "
           "artifacts, or cite a tracked packet")
# Entry fields the schema does not define as prose (not $defs/text): ids, enums, hashes, dates, urls, the
# structured subject, configuration, fingerprints, artifacts and bundle, and the structured source fields. A
# pointer names the field exactly (`[]` stands for any list index), so `physical.configuration` stays prose.
_SOURCE_FIELDS = ("accessed", "artifact", "date", "decision", "kind", "sha256", "url")
_WAIVER_FIELDS = ("decision", "expires", "obligation", "subject")
ENTRY_STRUCTURED = frozenset({
    "id", "requirement", "obligation", "subject", "level", "result", "configuration", "fingerprints", "artifacts",
    "bundle", "decisions", "physical.date",
    "criterion.id", "criterion.property", "criterion.operator", "criterion.value", "criterion.units", "criterion.state",
    *(f"source.{name}" for name in _SOURCE_FIELDS), *(f"criterion.source.{name}" for name in _SOURCE_FIELDS),
    *(f"waiver.{name}" for name in _WAIVER_FIELDS),
})
DISPOSITION_STRUCTURED = frozenset({
    "finding", "code", "producer", "status", "decision", "replacement_evidence",
    *(f"waiver.{name}" for name in _WAIVER_FIELDS),
})
_TOKEN = re.compile(r"[^\s\"'`()\[\]{}<>,;=|]+")
_EXTENSION = re.compile(r"\.(?:[A-Za-z][A-Za-z0-9]{0,7}|3mf|3ds|3dm|7z)$")
_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(.*)$")
_EVIDENCE_ID = re.compile(r"\bEV-[0-9]{3}\b")
_LOCATION = re.compile(r"[:#].*$")
_MANAGED_VERSION = re.compile(r"^hw/versions/v[1-9][0-9]*/.+")


@dataclass(frozen=True)
class Verdict:
    """Where one cited path lies. `rel` is the project-root-relative POSIX path when it resolves inside the
    root. `problem` says why the rule refuses it. `managed` says whether it is in a record-managed result location."""

    rel: str | None
    problem: str | None
    managed: bool = False
    exists: bool = True


@dataclass(frozen=True)
class PathProblem:
    code: str  # evidence.scratch_path or evidence.uncited_artifact
    where: str
    message: str


def is_path_like(token: str) -> bool:
    """Whether `token` has a path shape (see the module docstring)."""
    if token.startswith(SCRATCH_PREFIXES) or token == ".cad-work":
        return True
    if token.startswith("/"):
        return re.match(r"^/[^/]+/.+|^/[^/]+\.[A-Za-z]", token) is not None
    if "/" not in token:
        return False
    if ".." in token.split("/"):
        return True
    return _EXTENSION.search(token.rsplit("/", 1)[1]) is not None


def path_tokens(text: str) -> list[str]:
    """The path-like tokens of free text, with a trailing sentence mark, line number or anchor removed.
    URLs other than file:// are not file paths."""
    found = []
    for raw in _TOKEN.findall(text):
        token = _LOCATION.sub("", raw.rstrip(".:!?")) if not _URL.match(raw) else raw.rstrip(".:!?")
        match = _URL.match(token)
        if match:
            if match.group(1).lower() != "file":
                continue
            token = "/" + match.group(2).lstrip("/")
        if token and is_path_like(token):
            found.append(token)
    return found


class ProjectPaths:
    """The inside rule for one project root."""

    def __init__(self, root: Path, tracked: frozenset[str] | None, bundle_kinds: Iterable[str]) -> None:
        self.root = Path(root).resolve()
        self.bundle_kinds = frozenset(bundle_kinds)  # the version-bound bundle roots below hw/ (layout.bundle_kinds)
        self.tracked = tracked  # None: the project is no git work tree, so only the layout and scratch rules apply

    @classmethod
    def for_hw(cls, hw: Path, bundle_kinds: Iterable[str]) -> ProjectPaths:
        root = Path(hw).resolve().parent
        try:
            done = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
        except OSError:
            return cls(root, None, bundle_kinds)
        if done.returncode != 0:
            return cls(root, None, bundle_kinds)
        names = frozenset(name for name in done.stdout.decode("utf-8", "replace").split("\0") if name)
        return cls(root, names, bundle_kinds)

    def _is_tracked(self, rel: str) -> bool:
        assert self.tracked is not None
        return rel in self.tracked or any(name.startswith(f"{rel}/") for name in self.tracked)

    def judge(self, cited: str, *, structured: bool = False) -> Verdict:
        """The verdict on `cited` (project-root-relative, hw/-prefixed or hw/-relative, absolute or `~/`).
        A structured path that does not exist is not judged here: the digest reports it missing."""
        if cited.startswith("~"):
            return Verdict(None, "is in the home directory")
        candidates = [cited]
        if not structured and not cited.startswith(("/", "..", ".cad-work")) and not cited.startswith("hw/"):
            candidates.append(f"hw/{cited}")
        full = next((found for found in ((self.root / c).resolve() for c in candidates) if found.exists()),
                    (self.root / cited).resolve())
        if not full.is_relative_to(self.root):
            return Verdict(None, "is outside the project root")
        rel = full.relative_to(self.root).as_posix()
        parts = PurePosixPath(rel).parts
        if ".cad-work" in parts or rel == "hw/.cad" or rel.startswith("hw/.cad/"):
            return Verdict(rel, "is scratch space, not the record")
        exists = full.exists()
        managed = self._managed(rel)
        if not exists:
            return Verdict(rel, None if structured else "does not exist", managed, False)
        if not managed and self.tracked is not None and not self._is_tracked(rel):
            return Verdict(rel, "is not tracked by git and not a record result", False)
        return Verdict(rel, None, managed)

    def _managed(self, rel: str) -> bool:
        if any(part.startswith(".") for part in PurePosixPath(rel).parts):
            return False
        if _MANAGED_VERSION.match(rel):
            return True
        parts = PurePosixPath(rel).parts
        return (len(parts) >= 4 and parts[0] == "hw" and parts[1] in self.bundle_kinds
                and re.fullmatch(r"v[1-9][0-9]*", parts[2]) is not None)


def _strings(node: Any, skip: frozenset[str], key: str = "", pointer: str = "") -> Iterable[tuple[str, str]]:
    """(dotted key, text) of every string below `node` whose exact pointer is not in `skip`."""
    if pointer in skip:
        return
    if isinstance(node, str):
        yield key, node
    elif isinstance(node, Mapping):
        for name, value in node.items():
            yield from _strings(value, skip, f"{key}.{name}" if key else str(name), f"{pointer}.{name}" if pointer else str(name))
    elif isinstance(node, Sequence):
        for index, value in enumerate(node):
            yield from _strings(value, skip, f"{key}[{index}]", f"{pointer}[]")


def entry_problems(paths: ProjectPaths, entry: Mapping[str, Any]) -> list[PathProblem]:
    """Why an accepted evidence entry cites files the record cannot hold: a structured path or a path-like
    token of its text outside the inside rule, or a record result its text cites but its artifacts do not list."""
    problems: list[PathProblem] = []
    listed: list[str] = []
    structured = [(f"artifacts[{i}]", a.get("path")) for i, a in enumerate(entry.get("artifacts") or ())
                  if isinstance(a, Mapping)]
    bundle = entry.get("bundle")
    if isinstance(bundle, Mapping):
        structured.append(("bundle", bundle.get("path")))
    for where, holder in (("source.artifact", entry.get("source")),
                          ("criterion.source.artifact", (entry.get("criterion") or {}).get("source"))):
        if isinstance(holder, Mapping) and isinstance(holder.get("artifact"), str):
            problems.extend(_structured_problems(paths, [(where, f"hw/{holder['artifact']}")]))
    for where, cited in structured:
        if isinstance(cited, str):
            found = _structured_problems(paths, [(where, cited)])
            problems.extend(found)
            verdict = paths.judge(cited, structured=True)
            if not found and verdict.rel:
                listed.append(verdict.rel)
    problems.extend(_text_problems(paths, entry, listed, ENTRY_STRUCTURED))
    return problems


def _structured_problems(paths: ProjectPaths, cited: Iterable[tuple[str, str]]) -> list[PathProblem]:
    problems = []
    for where, path in cited:
        verdict = paths.judge(path, structured=True)
        if verdict.problem:
            problems.append(PathProblem("evidence.scratch_path", where, f"{where} {path} {verdict.problem}: {PROMOTE}"))
    return problems


def _text_problems(paths: ProjectPaths, node: Mapping[str, Any], listed: Sequence[str] | None,
                   skip: frozenset[str]) -> list[PathProblem]:
    problems: list[PathProblem] = []
    for key, text in _strings(node, skip):
        for token in path_tokens(text):
            verdict = paths.judge(token)
            if verdict.problem:
                problems.append(PathProblem("evidence.scratch_path", key,
                                            f"{key} cites {token}, which {verdict.problem}: {PROMOTE}"))
            elif listed is not None and verdict.managed and verdict.rel is not None \
                    and not any(verdict.rel == item or verdict.rel.startswith(f"{item}/") for item in listed):
                problems.append(PathProblem("evidence.uncited_artifact", key,
                                            f"{key} cites {verdict.rel}, a record result its artifacts do not list: "
                                            "list it in artifacts with its sha256"))
    return problems


def disposition_problems(paths: ProjectPaths, disposition: Mapping[str, Any]) -> list[PathProblem]:
    """The text-field problems of one finding disposition (it lists no artifacts, so only the inside rule applies)."""
    bundle = (disposition.get("producer") or {}).get("bundle")
    found = _structured_problems(paths, [("producer.bundle", bundle)] if isinstance(bundle, str) else [])
    return found + _text_problems(paths, disposition, None, DISPOSITION_STRUCTURED)


def evidence_refs(entry: Mapping[str, Any]) -> list[str]:
    """The other evidence ids that the prose of `entry` names, in order of first mention."""
    own = entry.get("id")
    found: dict[str, None] = {}
    for _, text in _strings(entry, ENTRY_STRUCTURED):
        found.update((ident, None) for ident in _EVIDENCE_ID.findall(text) if ident != own)
    return list(found)


def dependents(entries: Sequence[Mapping[str, Any]], refused: Iterable[str]) -> dict[str, list[str]]:
    """The entries that rest on a refused entry, directly or through other dependents, with the refused or
    dependent entries each names. The loop runs to a fixed point, so a cycle among accepted entries taints nobody."""
    roots = set(refused)
    tainted = set(roots)
    refs = {str(e.get("id")): evidence_refs(e) for e in entries}
    changed = True
    while changed:
        changed = False
        for ident, cited in refs.items():
            if ident not in tainted and any(c in tainted for c in cited):
                tainted.add(ident)
                changed = True
    return {ident: [c for c in cited if c in tainted] for ident, cited in refs.items()
            if ident in tainted and ident not in roots}
