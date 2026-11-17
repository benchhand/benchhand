"""Scripted user: find what the lead asked and answer it from a case's answers.yaml.

The lead stops for one of three reasons: it asks questions, it presents a gate, or it pauses with
neither. `respond` turns the lead's last reply into asks (question sentences, and lines that ask
for a reply or approval), matches each ask to the first intent whose pattern finds it, and builds
one reply. An unmatched ask gets the default answer and is recorded as unmatched. A reply with no
ask at all is a nudge, not a human wait.

answers.yaml shape (`extends: _common` prepends the case's intents to the shared file's and
overrides its scalars)::

    extends: _common
    default: <answer for an unmatched ask>
    nudge: <message when the lead asked nothing>
    wait_s: {question: 120, gate: 600, nudge: 30}   # simulated human time per reply kind
    stop: {fixture: layout-freeze, bench: parts-gate}  # intent whose answer ends the run, by mode
    intents:
      - id: storage
        kind: question            # or gate
        profile: false            # true for a flow-profile question
        match: ['results_in_git', 'generated results.*git']
        answer: <text>
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

KINDS = ("question", "gate")
MODES = ("fixture", "bench")
# A line that asks for a reply without a question mark: an imperative request at its start, or a
# direct "do you approve" anywhere. Narrative lines ("Once you answer, I'll ask for your approval")
# are not asks.
ASK_LINE = re.compile(
    r"^(?:please|reply|respond|confirm|approve|choose|select|pick|say|let me know|tell me)\b"
    r"|\b(?:do|can|could|would|will) you (?:approve|confirm)\b",
    re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
MARKUP = re.compile(r"^[\s>*#\-+|—–]*(?:\d+[.)]\s*)?|[*_`]+")
GATE_WORD = re.compile(r"\b(gate|freeze)\b", re.IGNORECASE)
APPROVAL_ASK = re.compile(r"\bapprov(?:e|al)\b", re.IGNORECASE)
FOLLOW_UP = re.compile(r"^(?:or|and|also|if so|if not|otherwise)\b", re.IGNORECASE)
STOP_NOTE = ("This ends the session: record this decision as instructed, commit, and stop. "
             "Do not start the next stage.")


class ScriptError(ValueError):
    """answers.yaml does not have the documented shape."""


@dataclass(frozen=True)
class Intent:
    id: str
    kind: str
    patterns: tuple[re.Pattern[str], ...]
    answer: str
    profile: bool = False

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.patterns)


@dataclass(frozen=True)
class Script:
    intents: tuple[Intent, ...]
    default: str
    nudge: str
    wait_s: Mapping[str, float]
    stop: Mapping[str, str]

    def profile_intents(self) -> tuple[str, ...]:
        return tuple(intent.id for intent in self.intents if intent.profile)


@dataclass(frozen=True)
class Ask:
    text: str
    intent: str | None
    kind: str | None
    profile: bool

    @property
    def matched(self) -> bool:
        return self.intent is not None


@dataclass(frozen=True)
class Reply:
    message: str
    asks: tuple[Ask, ...]
    kind: str
    wait_s: float
    stops: bool
    answered: tuple[str, ...] = field(default=())
    at_gate: bool = False

    @property
    def unmatched(self) -> tuple[Ask, ...]:
        return tuple(ask for ask in self.asks if not ask.matched)

    @property
    def unmatched_at_gate(self) -> bool:
        """An unmatched question in a gate prompt: the run fails rather than answer a gate by default."""
        return self.at_gate and bool(self.unmatched)


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScriptError(f"{where} must be a non-empty string")
    return value.strip()


def _intent(raw: Any, index: int) -> Intent:
    if not isinstance(raw, Mapping):
        raise ScriptError(f"intents[{index}] must be a mapping")
    ident = _text(raw.get("id"), f"intents[{index}].id")
    kind = raw.get("kind", "question")
    if kind not in KINDS:
        raise ScriptError(f"intent {ident}: kind must be one of {KINDS}")
    patterns = raw.get("match")
    if not isinstance(patterns, Sequence) or isinstance(patterns, str) or not patterns:
        raise ScriptError(f"intent {ident}: match must be a non-empty list of patterns")
    try:
        compiled = tuple(re.compile(_text(pattern, f"intent {ident} pattern"), re.IGNORECASE) for pattern in patterns)
    except re.error as error:
        raise ScriptError(f"intent {ident}: bad pattern: {error}") from error
    return Intent(ident, kind, compiled, _text(raw.get("answer"), f"intent {ident}.answer"), bool(raw.get("profile")))


def merge(case: Mapping[str, Any], common: Mapping[str, Any]) -> dict[str, Any]:
    """A case's answers over the shared ones.

    Order decides a match, first wins: the shared gates, then the case's own intents, then the
    shared questions. A shared intent without an `answer` is a slot: the case's `slots` mapping
    supplies its answer, and a slot the case leaves out is dropped, so the default answers it.
    """
    merged = {**common, **{key: value for key, value in case.items() if key not in ("intents", "extends", "slots")}}
    merged["wait_s"] = {**(common.get("wait_s") or {}), **(case.get("wait_s") or {})}
    merged["stop"] = {**(common.get("stop") or {}), **(case.get("stop") or {})}
    slots = case.get("slots") or {}
    shared: list[Any] = []
    for intent in common.get("intents") or ():
        if not isinstance(intent, Mapping) or "answer" in intent:
            shared.append(intent)
        elif intent.get("id") in slots:
            shared.append({**intent, "answer": slots[intent["id"]]})
    unknown = set(slots) - {intent.get("id") for intent in common.get("intents") or () if isinstance(intent, Mapping)}
    if unknown:
        raise ScriptError(f"slots without a shared intent: {sorted(unknown)}")
    gates = [intent for intent in shared if isinstance(intent, Mapping) and intent.get("kind") == "gate"]
    merged["intents"] = [*gates, *(case.get("intents") or ()), *(i for i in shared if i not in gates)]
    return merged


def load_script(data: Any) -> Script:
    """Validate a parsed answers.yaml."""
    if not isinstance(data, Mapping):
        raise ScriptError("answers.yaml must be a mapping")
    raw_intents = data.get("intents")
    if not isinstance(raw_intents, Sequence) or isinstance(raw_intents, str) or not raw_intents:
        raise ScriptError("intents must be a non-empty list")
    intents = tuple(_intent(raw, index) for index, raw in enumerate(raw_intents))
    ids = [intent.id for intent in intents]
    if len(set(ids)) != len(ids):
        raise ScriptError("intent ids must be unique")
    stop = data.get("stop") or {}
    if not isinstance(stop, Mapping) or set(stop) - set(MODES) or any(value not in ids for value in stop.values()):
        raise ScriptError(f"stop must map modes {MODES} to intent ids")
    wait = data.get("wait_s") or {}
    if not isinstance(wait, Mapping) or any(not isinstance(value, (int, float)) for value in wait.values()):
        raise ScriptError("wait_s must map reply kinds to seconds")
    return Script(intents, _text(data.get("default"), "default"), _text(data.get("nudge"), "nudge"),
                  {key: float(value) for key, value in wait.items()}, dict(stop))


def _clean(line: str) -> str:
    return MARKUP.sub("", line).strip()


def asks_in_context(text: str) -> list[tuple[str, str | None]]:
    """Question sentences and reply-requesting lines of the lead's reply, in order, each with the
    sentence before it when the ask is a follow-up that only makes sense with it ("Or kept out of git?")."""
    asks: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        cleaned = _clean(line)
        if not cleaned:
            continue
        pieces = [piece.strip() for piece in SENTENCE_END.split(cleaned) if piece.strip()]
        found = [(index, piece) for index, piece in enumerate(pieces) if piece.endswith("?")]
        if not found and ASK_LINE.search(cleaned):
            found = [(0, cleaned)]
        for index, piece in found:
            follow_up = index > 0 and (FOLLOW_UP.match(piece) is not None or len(piece.split()) <= 3)
            if piece not in (ask for ask, _context in asks):
                asks.append((piece, pieces[index - 1] if follow_up else None))
    return asks


def extract_asks(text: str) -> list[str]:
    """Question sentences and reply-requesting lines of the lead's reply, in order, without duplicates."""
    return [ask for ask, _context in asks_in_context(text)]


def classify(script: Script, ask: str, context: str | None = None) -> Ask:
    """The first intent that matches the ask, else, for a follow-up, the first that matches the sentence before it."""
    for candidate in (ask, context):
        if candidate is None:
            continue
        for intent in script.intents:
            if intent.matches(candidate):
                return Ask(ask, intent.id, intent.kind, intent.profile)
    return Ask(ask, None, None, False)


def presents_gate(asks: Sequence[Ask], text: str) -> bool:
    """The lead presents a gate: an ask matched a gate intent, or it asks for approval in a message naming a gate or freeze."""
    return any(ask.kind == "gate" for ask in asks) or (
        bool(GATE_WORD.search(text)) and any(APPROVAL_ASK.search(ask.text) for ask in asks))


def respond(script: Script, text: str, mode: str, can_stop: bool = True) -> Reply:
    """Build the scripted user's reply to the lead's last message.

    The reply ends the run when it answers the mode's stop intent and `can_stop` holds; the caller
    passes whether the record already has the version that gate approves, so a gate named too early
    is answered without ending the run.
    """
    asks = tuple(classify(script, ask, line) for ask, line in asks_in_context(text))
    if not asks:
        return Reply(script.nudge, asks, "nudge", script.wait_s.get("nudge", 0.0), False)
    by_id = {intent.id: intent for intent in script.intents}
    answered: list[str] = []
    for ask in asks:
        if ask.intent is not None and ask.intent not in answered:
            answered.append(ask.intent)
    lines = ["Answers from the user:", *(f"- {by_id[ident].answer}" for ident in answered)]
    if any(not ask.matched for ask in asks):
        lines.append(f"- {script.default}")
    kind = "gate" if any(by_id[ident].kind == "gate" for ident in answered) else "question"
    stops = can_stop and script.stop.get(mode) in answered
    if stops:
        lines.append(STOP_NOTE)
    return Reply("\n".join(lines), asks, kind, script.wait_s.get(kind, 0.0), stops, tuple(answered),
                 presents_gate(asks, text))
