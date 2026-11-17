"""Export tool-result findings as SARIF 2.1.0. The typed tool-result envelope stays the native format. The SARIF file is a
one-way export for tools that read SARIF. The export uploads nothing and uses no network.

emit.py loads this file by path and calls `document`. Exporting needs only the standard library.
`validation_errors` checks that a document matches the vendored OASIS schema, vendor/sarif-schema-2.1.0.json, whose
hash and source URL are in the .pin file beside it. That schema is JSON Schema version 4, so it has this one
validator and does not use the 2020-12 schema registry. jsonschema loads on the first `validation_errors` call, so
the export runs in FreeCAD's Python without it.

Mapping, per finding:
- ruleId: `code`, else `check`, `id` or `rule`, else UNCODED. Each distinct id gets one `tool.driver.rules` entry,
  in first-seen order. Its `help` is the first `remedy` seen for that id.
- level: critical and major map to error, minor to warning and info to note. The old words still emitted by
  validate.py, the records and the layout audit map as error to error, warning to warning and stage to warning.
  Without a severity, `blocking` true maps to error, false to warning and absent to error, because no severity
  counts as major in the shared target mapping. Any other word, or a non-string, maps to warning and sets
  `properties.unrecognizedSeverity`. The word as written stays in `properties.severity`.
- kind: outcome fail gives fail, unknown gives review, pass gives pass and not_applicable gives notApplicable. The
  last two have level none. No outcome, or an unknown one, leaves kind out.
- message.text: `message`, else "<rule>[: <part>] (the producer gave no message)". `remedy` goes to `properties.remedy`.
- properties.finding: the finding as the producer wrote it. Keys with no SARIF place, such as form_check's numbers, stay there.
- partialFingerprints["cadFinding/v1"]: the first 32 hex digits of sha256("code|PN|feature"). A missing part or
  feature counts as an empty field.
- locations: `location {file, line}` when valid, else line 1 of hw/parts/<PN>/part.yaml, else of
  hw/parts/<PN>/model.py. With neither file the result has no physical location, which SARIF allows, and stays in the
  output. The part and feature become logicalLocations in the same `locations` entry.
A finding that is not a mapping becomes an uncoded result whose message is its text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

VERSION = "2.1.0"
SCHEMA_URI = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
VENDORED_SCHEMA = Path(__file__).resolve().parent.parent / "vendor" / "sarif-schema-2.1.0.json"
FINGERPRINT_KEY = "cadFinding/v1"
UNCODED = "cad.finding.uncoded"
CODE_KEYS = ("code", "check", "id", "rule")
PART_KEYS = ("part", "pn", "PN")
LEVELS = {"critical": "error", "major": "error", "minor": "warning", "info": "note",
          "error": "error", "warning": "warning", "stage": "warning"}
KINDS = {"fail": "fail", "unknown": "review", "pass": "pass", "not_applicable": "notApplicable"}
QUIET_KINDS = frozenset({"pass", "notApplicable"})
PART_FILES = ("part.yaml", "model.py")
PART_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def document(findings: Iterable[Any], *, tool: str = "cad-agent", status: str | None = None,
             hw: Path | None = None) -> dict[str, Any]:
    """The SARIF 2.1.0 document of `findings`. `hw` is the project's hw/ directory and locates part files."""
    rules: dict[str, dict[str, Any]] = {}
    results = []
    for item in findings:
        result = _result(item if isinstance(item, Mapping) else {"message": str(item)}, hw)
        rule = rules.setdefault(result["ruleId"], {"id": result["ruleId"], "shortDescription": {"text": result["ruleId"]}})
        remedy = result["properties"].get("remedy")
        if remedy and "help" not in rule:
            rule["help"] = {"text": remedy, "markdown": remedy}
        result["ruleIndex"] = list(rules).index(result["ruleId"])
        results.append(result)
    run: dict[str, Any] = {"tool": {"driver": {"name": tool, "rules": list(rules.values())}}, "results": results}
    if status is not None:
        run["properties"] = {"status": status}
    return {"$schema": SCHEMA_URI, "version": VERSION, "runs": [run]}


def fingerprint(code: str, part: str | None, feature: str | None) -> str:
    """A stable identity of a finding across runs: the same code, part and feature give the same value."""
    return hashlib.sha256(f"{code}|{part or ''}|{feature or ''}".encode()).hexdigest()[:32]


def validation_errors(doc: Any) -> list[str]:
    """Messages as path and message, for `doc` against the vendored OASIS schema. Empty when `doc` is valid."""
    return sorted(f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in _validator().iter_errors(doc))


@lru_cache(maxsize=1)
def _validator() -> Any:
    """The one validator of the vendored version-4 schema, built on first use."""
    from jsonschema import Draft4Validator  # imported here, so exporting does not need jsonschema

    return Draft4Validator(json.loads(VENDORED_SCHEMA.read_text(encoding="utf-8")))


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first(item: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    return next((text for key in keys if (text := _text(item.get(key)))), None)


def _subject(item: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The part number and feature. `subject` comes first, then the old keys `part`, `pn`, `PN` and `feature`."""
    subject = item.get("subject") if isinstance(item.get("subject"), Mapping) else {}
    return (_text(subject.get("PN")) or _first(item, PART_KEYS), _text(subject.get("feature")) or _text(item.get("feature")))


def _level(item: Mapping[str, Any]) -> tuple[str, bool]:
    """The SARIF level and a flag for a severity word the table lacks."""
    if "severity" not in item or item["severity"] is None:
        blocking = item.get("blocking", True)
        return ("error" if blocking else "warning"), False
    word = item["severity"]
    level = LEVELS.get(word) if isinstance(word, str) else None
    return (level, False) if level else ("warning", True)


def _result(item: Mapping[str, Any], hw: Path | None) -> dict[str, Any]:
    code = _first(item, CODE_KEYS) or UNCODED
    part, feature = _subject(item)
    level, unrecognised = _level(item)
    kind = KINDS.get(item["outcome"]) if isinstance(item.get("outcome"), str) else None
    message = _text(item.get("message")) or f"{code}{f': {part}' if part else ''} (the producer gave no message)"
    properties: dict[str, Any] = {"finding": dict(item)}
    if item.get("severity") is not None:
        properties["severity"] = item["severity"]
    if unrecognised:
        properties["unrecognizedSeverity"] = True
    if remedy := _text(item.get("remedy")):
        properties["remedy"] = remedy
    result: dict[str, Any] = {"ruleId": code, "level": "none" if kind in QUIET_KINDS else level,
                              "message": {"text": message}, "partialFingerprints": {FINGERPRINT_KEY: fingerprint(code, part, feature)},
                              "properties": properties}
    if kind:
        result["kind"] = kind
    location: dict[str, Any] = {}
    if physical := _physical(item, part, hw):
        location["physicalLocation"] = physical
    if logical := _logical(part, feature):
        location["logicalLocations"] = logical
    if location:
        result["locations"] = [location]
    return result


def _physical(item: Mapping[str, Any], part: str | None, hw: Path | None) -> dict[str, Any] | None:
    """Where the result anchors: the finding's own file and line, else line 1 of the part's file, else nowhere."""
    location = item.get("location")
    if isinstance(location, Mapping) and (file := _text(location.get("file"))):
        line = location.get("line")
        return _region(file.replace("\\", "/"), line if type(line) is int and line >= 1 else 1)
    uri = _part_file(part, hw)
    return _region(uri, 1) if uri else None


def _region(uri: str, line: int) -> dict[str, Any]:
    return {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}


def _part_file(part: str | None, hw: Path | None) -> str | None:
    """`<hw name>/parts/<PN>/part.yaml`, else `model.py`, when it exists under `hw`."""
    if hw is None or part is None or not PART_NUMBER.match(part):
        return None
    root = Path(hw).resolve()
    return next((f"{root.name}/parts/{part}/{name}" for name in PART_FILES if (root / "parts" / part / name).is_file()), None)


def _logical(part: str | None, feature: str | None) -> list[dict[str, str]]:
    out = []
    if part:
        out.append({"name": part, "kind": "module", "fullyQualifiedName": part})
    if feature:
        out.append({"name": feature, "kind": "member", "fullyQualifiedName": f"{part}/{feature}" if part else feature})
    return out
