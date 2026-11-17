"""Print a producer's JSON result in full for a person or a script, or as a short summary for an agent.

`cad-flow/scripts/run.py` sets `CAD_AGENT_OUTPUT=1` for every tool it launches. A producer then prints the
verdict, its first blocking findings and the path of the full JSON instead of the whole document. The full
JSON is written byte for byte as the plain mode would print it, so no information is lost. Without the
variable the output is exactly `json.dumps(result, indent=2)` and a newline.

A tool that parses another producer's stdout must start it with `child_env()`, so it always gets full JSON.

The module is standard-library only and lives in cadmodel, the package every skill script already imports
from a sibling `cad-model/scripts` directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TextIO

ENV = "CAD_AGENT_OUTPUT"
MAX_BLOCKING = 5
MAX_LINE = 200
NONBLOCKING_SEVERITIES = frozenset({"warning", "info"})
IDENTITY_KEYS = ("code", "check", "id", "rule")
SUBJECT_KEYS = ("part", "joint", "subject", "path")


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get(ENV, "") not in ("", "0")


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """`env` (default: this process's) without the agent switch: for a child whose stdout this tool parses."""
    return {key: value for key, value in (os.environ if env is None else env).items() if key != ENV}


def work_dir(hw: Path) -> Path:
    """Where the full JSON goes when the producer has no --out: the project's .cad-work, beside hw/."""
    return Path(hw).resolve().parent / ".cad-work" / "agent-output"


def is_blocking(item: Mapping[str, Any]) -> bool:
    """A finding's own `blocking` flag, else any severity that is not a warning or info."""
    if "blocking" in item:
        return bool(item["blocking"])
    return item.get("severity") not in NONBLOCKING_SEVERITIES


def describe(item: Any) -> str:
    """One bounded line for a finding: identity, subject, then the message."""
    if not isinstance(item, Mapping):
        return _clip(str(item))
    parts = [str(item[key]) for keys in (IDENTITY_KEYS, SUBJECT_KEYS) for key in keys if item.get(key) is not None][:3]
    if item.get("message"):
        parts.append(str(item["message"]))
    return _clip(": ".join(parts) if parts else json.dumps(item, sort_keys=True))


def blocking_of(result: Mapping[str, Any]) -> list[Any]:
    """The blocking items of a report: its `blocking_failures`, else the blocking entries of `findings`."""
    failures = result.get("blocking_failures")
    if isinstance(failures, list):
        return failures
    findings = result.get("findings")
    return [f for f in findings if not isinstance(f, Mapping) or is_blocking(f)] if isinstance(findings, list) else []


def verdict_of(result: Mapping[str, Any]) -> str:
    if "status" in result:
        return str(result["status"])
    return "pass" if result.get("ok") else "fail" if "ok" in result else "unknown"


def emit(result: Any, *, tool: str, name: str, code: int, scratch: Path | None = None, out: Path | None = None,
         facts: Mapping[str, Any] | None = None, blocking: Iterable[Any] | None = None,
         stream: TextIO | None = None) -> None:
    """Print `result`: in full, or as a summary when the agent switch is on.

    `out` is a file that already holds exactly this result's plain text; otherwise the full text goes to a
    file in `scratch` named from `name` and a digest. When neither exists or the write fails, the full JSON is
    printed, so nothing is lost; so is a result that is no longer than its summary. `facts` are short key/value lines; `blocking` replaces the items found by
    `blocking_of`.
    """
    stream = sys.stdout if stream is None else stream
    text = json.dumps(result, indent=2)
    if not enabled():
        print(text, file=stream)
        return
    mapping = result if isinstance(result, Mapping) else {}
    holder = out.resolve() if out is not None and _holds(out, text) else None
    target = holder or (None if scratch is None else _scratch_file(text, name, scratch))
    if target is None:
        print(text, file=stream)
        return
    items = list(blocking_of(mapping) if blocking is None else blocking)
    lines = [f"{tool}: {verdict_of(mapping)} (exit {code})"]
    lines += [_clip(f"{key}: {value}") for key, value in (facts or {}).items() if value not in (None, "", [], {})]
    if items:
        lines.append(f"blocking ({len(items)}):")
        lines += [f"  - {describe(item)}" for item in items[:MAX_BLOCKING]]
        if len(items) > MAX_BLOCKING:
            lines.append(f"  +{len(items) - MAX_BLOCKING} more")
    lines.append(f"full JSON: {target}")
    summary = "\n".join(lines)
    if len(text) <= len(summary) or (holder is None and not _write(target, text)):
        print(text, file=stream)  # a result no longer than its summary, or a file that cannot be written
        return
    print(summary, file=stream)


def _scratch_file(text: str, name: str, scratch: Path) -> Path:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return (Path(scratch) / f"{name}-{digest}.json").resolve()


def _write(target: Path, text: str) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _holds(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == text + "\n"
    except OSError:
        return False


def _clip(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= MAX_LINE else line[: MAX_LINE - 1] + "…"

