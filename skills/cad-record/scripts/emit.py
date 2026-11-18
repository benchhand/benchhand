# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

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

`sarif=True` (or `emit.py --sarif ENVELOPE.json [--hw DIR]` on a stored envelope) prints the envelope's findings as
SARIF 2.1.0 instead, in full whatever the mode (see sarif.py for the mapping). It is an export only: `out` still gets
the envelope, and the default output does not change.

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
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


def sarif_text(result: Any, *, tool: str = "benchhand", hw: Path | None = None) -> str:
    """The findings of `result` as SARIF 2.1.0 JSON, without a trailing newline. `hw` lets part findings anchor on a file."""
    document = envelope(result)
    return json.dumps(_sarif().document(document["findings"], tool=tool, status=document["status"], hw=hw), indent=2)


def _sarif() -> Any:
    """The sibling sarif.py, loaded by path as every cad-record script is. A caller that never asks for SARIF never loads it."""
    name = "cad_record_sarif"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("sarif.py"))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


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
         json_flag: bool = False, schema_id: str | None = SCHEMA, stream: TextIO | None = None,
         sarif: bool = False, hw: Path | None = None) -> None:
    """Print `result` in its envelope: in full, or as a summary in `compact` mode.

    `out` is the producer's `--out` file: it always gets the enveloped full JSON. Without `out`, the compact
    summary names a file in `scratch` made from `name` and a digest. When neither can hold the full JSON, or it
    is no longer than its summary, the full JSON is printed, so nothing is lost. `facts` are short key/value
    lines. `blocking` replaces the items found by `blocking_of`. `sarif` prints the findings as SARIF instead, see
    `sarif_text`. `hw` is the project's hw/ directory.
    """
    stream = sys.stdout if stream is None else stream
    text = envelope_text(result, schema_id)
    if out is not None:
        out.write_text(text + "\n", encoding="utf-8")
    if sarif:
        print(sarif_text(result, tool=tool, hw=hw), file=stream)
        return
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
    abstained = mapping.get("abstentions")
    if isinstance(abstained, list) and abstained:
        lines.append(_clip(f"abstentions ({len(abstained)}): {', '.join(str(name) for name in abstained)}"))
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


def main(argv: list[str] | None = None) -> int:
    """`emit.py ENVELOPE.json --sarif [--hw DIR]`: print a stored envelope's findings as SARIF 2.1.0. Exit 2 on bad input."""
    parser = argparse.ArgumentParser(prog="emit.py", description=main.__doc__)
    parser.add_argument("envelope", type=Path, help="a stored tool-result envelope (JSON)")
    parser.add_argument("--sarif", action="store_true", required=True, help="export the findings as SARIF 2.1.0")
    parser.add_argument("--hw", type=Path, help="the project's hw/ directory, to anchor part findings on part.yaml")
    parser.add_argument("--tool", default="benchhand", help="the SARIF tool name")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return int(stop.code or 0)
    try:
        stored = json.loads(args.envelope.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"emit.py: cannot read {args.envelope}: {error}", file=sys.stderr)
        return 2
    print(sarif_text(stored, tool=args.tool, hw=args.hw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

