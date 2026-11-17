"""Failed runs of the repository's own record and check tools, kept from the session's tool results.

The sanitized session exports drop every tool output, so a failing `stamp.py --verify` or `validate.py`
loses its diagnostics. `collect` reads the unsanitized exports (or `opencode run --format json` events)
and keeps, for each shell call that runs a covered tool and exits non-zero, the command, the exit code and
the tail of its output. OpenCode's shell tool reports stdout and stderr merged in one text, so the tail is
of the merged output. When OpenCode truncated a long output it names the file holding the whole of it;
that file is read when it lies inside the run's own OpenCode home.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

JSON = dict[str, Any]

TAIL_BYTES = 4096
NAMED_TOOLS = ("stamp.py", "validate.py", "cad_model.py", "scratch.py", "form_check.py", "evidence.py", "flow.py")
COVERED = re.compile(r"(?:^|[\s/])(?:" + "|".join(re.escape(name) for name in NAMED_TOOLS) + r")(?=\s|$|[;&|)])"
                     r"|\brun\.py\s+(?:\S*/)?cad-[a-z-]+/scripts/[\w./-]+\.py")
EXIT_LINE = re.compile(r"^Exit code: -?\d+\s*$", re.MULTILINE)
SAVED_OUTPUT = re.compile(r"full output saved to ([^\s\]]+)")
COMPOUND = re.compile(r"&&|\|\||[;|]|<<|\n")
SECRETS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[redacted:private-key]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[redacted:aws-key-id]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"), "[redacted:github-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[redacted:api-key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted:slack-token]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]{16,}=*"), r"\1 [redacted]"),
    (re.compile(r"(?i)\b([\w-]*(?:secret|token|password|passwd|api[_-]?key|access[_-]?key)[\w-]*)(\"?\s*[:=]\s*\"?)[^\s\"',;]{8,}"),
     r"\1\2[redacted]"),
)


def redact(text: str) -> str:
    """Mask anything shaped like a key, token or password; sha256 digests and paths stay."""
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def covered(command: str) -> bool:
    return bool(COVERED.search(command))


def _state(part: Mapping[str, Any]) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    """Tool name, state and metadata of an export tool part or a run event's part."""
    state = part.get("state") or {}
    metadata = state.get("metadata") or {}
    if isinstance(metadata.get("metadata"), Mapping):  # event stream: metadata.metadata holds exit and status
        metadata = {**metadata, **metadata["metadata"]}
    return str(part.get("name") or part.get("tool") or ""), state, metadata


def _output(state: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    items = state.get("content") or metadata.get("content") or ()
    texts = [str(item.get("text") or "") for item in items if isinstance(item, Mapping)]
    text = "\n".join(texts) if texts else str(state.get("output") or "")
    return EXIT_LINE.sub("", text).rstrip()


def _full_output(text: str, home: Path) -> str:
    """The saved whole output OpenCode names for a truncated result, when it lies inside the run's home."""
    match = SAVED_OUTPUT.search(text)
    if match is None:
        return text
    path = Path(match.group(1))
    if home not in path.parents or not path.is_file():
        return text
    return path.read_text(encoding="utf-8", errors="replace").rstrip()


def tail(text: str, size: int = TAIL_BYTES) -> str:
    return text.encode("utf-8")[-size:].decode("utf-8", errors="ignore")


def relative(text: str, roots: Mapping[str, str]) -> str:
    """Replace each absolute root (longest first) with its short name."""
    for path, name in sorted(roots.items(), key=lambda item: -len(item[0])):
        text = text.replace(path + "/", name + "/" if name else "").replace(path, name or ".")
    return text


def _roots(project: Path, scratch: Path, home: Path) -> dict[str, str]:
    roots: dict[str, str] = {}
    for path, name in ((home / ".config/opencode/skills", "$CAD_SKILLS"), (project, ""), (scratch, "<scratch>")):
        for form in {str(path), str(path).removeprefix("/private")}:
            roots[form] = name
    return roots


def _argv(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return [command]


def _parts(sources: Iterable[Mapping[str, Any]]) -> Iterable[tuple[str | None, Mapping[str, Any]]]:
    """(session id, tool part) for every tool part of the exports and events given."""
    for source in sources:
        if "messages" in source:
            session = (source.get("info") or {}).get("id")
            for message in source.get("messages") or ():
                for part in message.get("content") or ():
                    if isinstance(part, Mapping) and part.get("type") == "tool":
                        yield session, part
        elif source.get("type") == "tool_use":
            yield source.get("sessionID"), source.get("part") or {}


def collect(sources: Iterable[Mapping[str, Any]], project: Path, scratch: Path, home: Path) -> list[JSON]:
    """One record per failed covered tool call, in the order the sources list them, each call once."""
    roots, records, seen = _roots(project, scratch, home), [], set()
    for session, part in _parts(sources):
        name, state, metadata = _state(part)
        command = str((state.get("input") or {}).get("command") or "")
        code = metadata.get("exit")
        if name != "shell" or not covered(command) or not isinstance(code, int) or isinstance(code, bool) or code == 0:
            continue
        key = str(part.get("id") or part.get("partID") or id(part))
        if key in seen:
            continue
        seen.add(key)
        output = _full_output(_output(state, metadata), home)
        records.append({
            "session": session, "call": key,
            "argv": [redact(relative(arg, roots)) for arg in _argv(command)],
            "exit_code": code, "streams": "merged",
            # In a compound command the exit code may come from another part than the covered tool.
            "compound": bool(COMPOUND.search(command)),
            "output_tail": tail(redact(relative(output, roots))),
            "truncated": bool(metadata.get("truncated")),
        })
    return records
