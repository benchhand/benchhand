"""Acceptance of one run: hard checks the harness runs, and a rubric a separate judge model scores.

A run is accepted when every hard check passed and the judge marked every must-have requirement met.
The judge sees only the record, the check results and the renders, never the transcript.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

JSON = dict[str, Any]

STAGE_LEVEL = {"layout": "L1", "parts": "L2", "detail": "L3", "analysis": "L4"}
VERSION_LABEL = re.compile(r"^v([1-9][0-9]*)$")
JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class RubricError(ValueError):
    """requirements.yaml or a judge reply does not have the expected shape."""


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    must: bool = True


@dataclass(frozen=True)
class Rubric:
    requirements: tuple[Requirement, ...]
    sweep: bool


def load_rubric(data: Any) -> Rubric:
    if not isinstance(data, Mapping):
        raise RubricError("requirements.yaml must be a mapping")
    raw = data.get("requirements")
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise RubricError("requirements must be a non-empty list")
    requirements = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str):
            raise RubricError(f"requirements[{index}] needs a string id and text")
        requirements.append(Requirement(item["id"], item["text"].strip(), bool(item.get("must", True))))
    ids = [requirement.id for requirement in requirements]
    if len(set(ids)) != len(ids):
        raise RubricError("requirement ids must be unique")
    checks = data.get("hard_checks") or {}
    if not isinstance(checks, Mapping):
        raise RubricError("hard_checks must be a mapping")
    return Rubric(tuple(requirements), bool(checks.get("sweep", False)))


def version_number(label: str) -> int:
    match = VERSION_LABEL.match(label)
    if not match:
        raise ValueError(f"not a version label: {label}")
    return int(match.group(1))


def latest_at_stage(versions: Mapping[str, str | None], stage: str) -> str | None:
    """The newest version label whose record snapshot was built at `stage`."""
    labels = [label for label, built_at in versions.items() if built_at == stage and VERSION_LABEL.match(label)]
    return max(labels, key=version_number, default=None)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    exit_code: int | None = None

    def to_json(self) -> JSON:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "exit_code": self.exit_code}


def form_results(report: Mapping[str, Any] | None, exit_code: int | None) -> list[CheckResult]:
    """The F2 fidelity check and the process packet, from form_check.py --version stdout.

    The fidelity status must not be `fail`; the manufacturing packet must not be `fail`
    (`unknown` coverage is not FAIL). A run that printed no report fails both.
    """
    if not isinstance(report, Mapping):
        detail = f"form_check printed no report (exit {exit_code})"
        return [CheckResult("form_check_f2", False, detail, exit_code), CheckResult("process_packet", False, detail, exit_code)]
    fidelity = str(report.get("status"))
    manufacturing = report.get("manufacturing") if isinstance(report.get("manufacturing"), Mapping) else {}
    packet = str((manufacturing or {}).get("status"))
    return [
        CheckResult("form_check_f2", fidelity not in ("fail", "error"), f"fidelity status {fidelity}", exit_code),
        CheckResult("process_packet", packet not in ("fail", "None", "not_assessed"), f"packet result {packet}", exit_code),
    ]


def hard_verdict(results: Sequence[CheckResult]) -> JSON:
    failed = [result.name for result in results if not result.passed]
    return {"passed": bool(results) and not failed, "failed": failed, "checks": [result.to_json() for result in results]}


def judge_prompt(rubric: Rubric, record: Mapping[str, str], checks: Mapping[str, Any], renders: Sequence[str]) -> str:
    """The judge's whole input besides the attached render images."""
    lines = [
        "You are an independent reviewer of a mechanical CAD design record. Judge each requirement below as met or",
        "unmet using only the record files, the check results and the attached renders. A requirement is met only when",
        "the record or the geometry shows it; a claim without support, an open question or an unknown value is unmet.",
        "You have no tools; do not ask questions.",
        "",
        "Reply with one JSON object and nothing else:",
        '{"verdicts": [{"id": "<requirement id>", "met": true|false, "reason": "<one sentence citing the evidence>"}]}',
        "",
        "## Requirements",
        *(f"- {requirement.id}: {requirement.text}" for requirement in rubric.requirements),
        "",
        "## Attached renders",
        *(f"- {name}" for name in renders),
        "",
        "## Check results (JSON)",
        json.dumps(checks, indent=1, sort_keys=True),
        "",
        "## Record files",
    ]
    for path, text in record.items():
        lines += [f"### {path}", "```", text.rstrip(), "```"]
    return "\n".join(lines) + "\n"


def _json_object(text: str) -> Any:
    block = JSON_BLOCK.search(text)
    candidate = block.group(1) if block else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as error:
        raise RubricError(f"judge reply holds no JSON object: {error}") from error


def parse_verdicts(text: str, rubric: Rubric) -> dict[str, JSON]:
    """Each requirement's verdict from the judge reply; every requirement needs exactly one boolean verdict."""
    data = _json_object(text)
    items = data.get("verdicts") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        raise RubricError("judge reply needs a verdicts list")
    verdicts: dict[str, JSON] = {}
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("met"), bool) or not isinstance(item.get("id"), str):
            raise RubricError(f"malformed verdict: {item!r}")
        if item["id"] in verdicts:
            raise RubricError(f"two verdicts for {item['id']}")
        verdicts[item["id"]] = {"met": item["met"], "reason": str(item.get("reason") or "")}
    expected = {requirement.id for requirement in rubric.requirements}
    if set(verdicts) != expected:
        raise RubricError(f"verdict ids {sorted(verdicts)} differ from requirements {sorted(expected)}")
    return verdicts


def accepted(hard: Mapping[str, Any], verdicts: Mapping[str, Mapping[str, Any]] | None, rubric: Rubric) -> bool:
    if not hard.get("passed") or verdicts is None:
        return False
    return all(verdicts[requirement.id]["met"] for requirement in rubric.requirements if requirement.must)
