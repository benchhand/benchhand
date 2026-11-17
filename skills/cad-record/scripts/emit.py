"""Print a producer's result as full JSON or as a short summary for an agent.

The one output emitter of the CAD skills. A producer loads it by path from cad-record's scripts directory
(`<skills>/cad-record/scripts/emit.py`, reachable from every skill through its own file path) and calls `emit`.

Mode, by precedence: the `--json` flag (`json_flag=True`), then the `CAD_OUTPUT` environment variable
(`text`, `compact` or `json`), then the stream default: `compact` when stdout is a terminal, else `json`.
`cad-flow/scripts/run.py` sets `CAD_OUTPUT=compact` for the tools it launches for an agent.

- `json`: the tool-result envelope, `json.dumps(envelope(result), indent=2)` and a newline.
- `compact`: the verdict, the first blocking findings and the path of the full JSON, which is written byte for
  byte as `json` mode prints it, so no information is lost. A result no longer than its summary prints whole.
- `text` is accepted and renders as `compact` until a producer gets a dedicated text renderer.

The envelope is `{"schema", "status", "findings", "payload"}`: `payload` is the producer's result unchanged. It
wraps stdout and non-sealed `--out` files only. Sealed bundle and version files keep their own shape.

A tool that parses another producer's stdout must start it with `json_env()` and read it with `unwrap()`.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TextIO

ENV = "CAD_OUTPUT"
SCHEMA = "tool-result/1"
ENVELOPE_KEYS = frozenset({"schema", "status", "findings", "payload"})
MODES = ("text", "compact", "json")
PIPED_DEFAULT, TERMINAL_DEFAULT = "json", "compact"
MAX_BLOCKING = 5
MAX_LINE = 200
NONBLOCKING_SEVERITIES = frozenset({"warning", "info"})
IDENTITY_KEYS = ("code", "check", "id", "rule")
SUBJECT_KEYS = ("part", "joint", "subject", "path")


def stdout_isatty() -> bool:
    """Whether stdout is a terminal; tests replace it so they do not depend on theirs."""
    return sys.stdout.isatty()


def mode(json_flag: bool = False, env: Mapping[str, str] | None = None, isatty: bool | None = None) -> str:
    """The output mode, `json` or `compact`: `--json`, else `CAD_OUTPUT` when it names a mode, else by stdout."""
    if json_flag:
        return "json"
    value = (os.environ if env is None else env).get(ENV, "")
    if value in MODES:
        return "compact" if value == "text" else value  # no text renderer yet: the compact summary is the human view
    return TERMINAL_DEFAULT if (stdout_isatty() if isatty is None else isatty) else PIPED_DEFAULT


def json_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """`env` (default: this process's) with full JSON requested: for a child whose stdout this tool parses."""
    return {**(os.environ if env is None else env), ENV: "json"}


def validate_result(result: Any, schema_id: str | None) -> None:
    """Hook: check the envelope `result` against the schema named `schema_id` before it is printed or written.

    Package G implements it. Until then it accepts everything."""


def findings_of(result: Mapping[str, Any]) -> list[Any]:
    """All findings of a report: its `findings`, else its `blocking_failures`."""
    for key in ("findings", "blocking_failures"):
        if isinstance(result.get(key), list):
            return list(result[key])
    return []


def envelope(result: Any) -> dict[str, Any]:
    """`result` in the tool-result envelope."""
    mapping = result if isinstance(result, Mapping) else {}
    return {"schema": SCHEMA, "status": verdict_of(mapping), "findings": findings_of(mapping), "payload": result}


def envelope_text(result: Any, schema_id: str | None = SCHEMA) -> str:
    """The enveloped full JSON of `result`, as every non-sealed file and stdout hold it (no trailing newline)."""
    document = envelope(result)
    validate_result(document, schema_id)
    return json.dumps(document, indent=2)


def unwrap(document: Any) -> Any:
    """The payload of an envelope; any other document (a producer not yet switched) as it is."""
    if isinstance(document, dict) and set(document) == ENVELOPE_KEYS and str(document["schema"]).startswith("tool-result/"):
        return document["payload"]
    return document


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
         json_flag: bool = False, schema_id: str | None = SCHEMA, stream: TextIO | None = None) -> None:
    """Print `result` in its envelope: in full, or as a summary in `compact` mode.

    `out` is the producer's `--out` file: it always gets the enveloped full JSON. Without `out`, the compact
    summary names a file in `scratch` made from `name` and a digest. When neither can hold the full JSON, or it
    is no longer than its summary, the full JSON is printed, so nothing is lost. `facts` are short key/value
    lines; `blocking` replaces the items found by `blocking_of`.
    """
    stream = sys.stdout if stream is None else stream
    text = envelope_text(result, schema_id)
    if out is not None:
        out.write_text(text + "\n", encoding="utf-8")
    if mode(json_flag) != "compact":
        print(text, file=stream)
        return
    mapping = result if isinstance(result, Mapping) else {}
    target = out.resolve() if out is not None else (None if scratch is None else _scratch_file(text, name, scratch))
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
    if len(text) <= len(summary) or (out is None and not _write(target, text)):
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


def _clip(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= MAX_LINE else line[: MAX_LINE - 1] + "…"

