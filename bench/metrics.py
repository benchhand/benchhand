"""Throughput metrics of one benchmark run, read from OpenCode session exports.

An export is the JSON `opencode session export <id>` prints: `info` (the session, with `parentID`
for a subagent) and `messages`. An assistant message carries `tokens`, `cost`, `model` and `time`;
its `content` holds `text` and `tool` parts. This module reads that shape only and computes
nothing from the transcript text beyond the unit id of a subagent brief.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

JSON = dict[str, Any]

MILLION = 1_000_000
BUILD_COMMAND = re.compile(r"cad_model\.py\s+run\b|scratch\.py\s+build\b")
FAILED_BUILD_EXITS = (1, 2)
EXIT_LINE = re.compile(r"^Exit code: (-?\d+)\s*$", re.MULTILINE)
UNIT_LABEL = re.compile(r"\bunit(?:\s+id)?\s*[:=]?\s*[`\"']?([a-z][a-z0-9]*(?:-[a-z0-9]+)+)", re.IGNORECASE)
UNIT_TOKEN = re.compile(r"\b((?:variant|part|source|detail|analysis|clerk|layout|review)-[a-z0-9]+(?:-[a-z0-9]+)*)")
RETRY_SUFFIX = re.compile(r"-r\d+$")
SUBAGENT_TOOL = "subagent"
SHELL_TOOL = "shell"


class PriceError(ValueError):
    """A price table entry is malformed or a model has no price."""


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True)
class ModelPrice:
    """A base price and context-size tiers; a request whose context exceeds a tier's size pays that tier."""

    base: Price
    tiers: tuple[tuple[int, Price], ...] = ()

    def for_context(self, context: int) -> Price:
        price = self.base
        for size, tier in self.tiers:
            if context > size:
                price = tier
        return price


def _price(entry: Mapping[str, Any], where: str) -> Price:
    try:
        values = {key: float(entry[key]) for key in ("input", "output", "cache_read")}
    except (KeyError, TypeError, ValueError) as error:
        raise PriceError(f"{where}: needs numeric input, output and cache_read") from error
    write = entry.get("cache_write", values["input"])
    return Price(values["input"], values["output"], values["cache_read"], float(write))


def load_prices(table: Mapping[str, Any]) -> dict[str, ModelPrice]:
    """Parse `{"provider/model": {"input":..,"output":..,"cache_read":..,"cache_write":..,"tiers":[..]}}`."""
    prices: dict[str, ModelPrice] = {}
    for model, entry in table.items():
        if not isinstance(entry, Mapping):
            raise PriceError(f"{model}: price entry must be an object")
        tiers = []
        for index, tier in enumerate(entry.get("tiers") or ()):
            size = tier.get("context_over") if isinstance(tier, Mapping) else None
            if not isinstance(size, int) or isinstance(size, bool):
                raise PriceError(f"{model} tier {index}: needs an integer context_over")
            tiers.append((size, _price(tier, f"{model} tier {index}")))
        prices[model] = ModelPrice(_price(entry, model), tuple(sorted(tiers, key=lambda item: item[0])))
    return prices


@dataclass(frozen=True)
class Tokens:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def of(cls, message: Mapping[str, Any]) -> Tokens:
        raw = message.get("tokens") or {}
        cache = raw.get("cache") or {}
        return cls(int(raw.get("input") or 0), int(raw.get("output") or 0), int(raw.get("reasoning") or 0),
                   int(cache.get("read") or 0), int(cache.get("write") or 0))

    @property
    def context(self) -> int:
        """Every input token the request sent, cached or not."""
        return self.input + self.cache_read + self.cache_write

    def cost(self, price: ModelPrice) -> float:
        tier = price.for_context(self.context)
        return (self.input * tier.input + (self.output + self.reasoning) * tier.output
                + self.cache_read * tier.cache_read + self.cache_write * tier.cache_write) / MILLION


def model_key(message: Mapping[str, Any]) -> str:
    model = message.get("model") or {}
    return f"{model.get('providerID', '?')}/{model.get('id', '?')}"


def assistant_messages(export: Mapping[str, Any]) -> list[JSON]:
    return [message for message in export.get("messages") or () if message.get("type") == "assistant"]


def tool_parts(message: Mapping[str, Any]) -> list[JSON]:
    return [part for part in message.get("content") or () if part.get("type") == "tool"]


def shell_exit(part: Mapping[str, Any]) -> int | None:
    state = part.get("state") or {}
    code = (state.get("metadata") or {}).get("exit")
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    for item in state.get("content") or ():
        match = EXIT_LINE.search(str(item.get("text") or ""))
        if match:
            return int(match.group(1))
    return None


def is_failed_build(part: Mapping[str, Any]) -> bool:
    if part.get("name") != SHELL_TOOL:
        return False
    command = str(((part.get("state") or {}).get("input") or {}).get("command") or "")
    return bool(BUILD_COMMAND.search(command)) and shell_exit(part) in FAILED_BUILD_EXITS


def unit_id(dispatch: Mapping[str, Any]) -> str:
    """The cad-flow unit a subagent brief names, without its `-rN` retry suffix.

    The brief's `unit`/`unit id` label wins, then the first unit-shaped token (`part-arm`), then
    the agent and description, so two briefs for one unit compare equal.
    """
    prompt = str(dispatch.get("prompt") or "")
    match = UNIT_LABEL.search(prompt) or UNIT_TOKEN.search(prompt)
    if match:
        return RETRY_SUFFIX.sub("", match.group(1).lower())
    return f"{dispatch.get('agent', '?')}:{str(dispatch.get('description') or '').strip().lower()}"


def _ms(time: Mapping[str, Any], start: str, end: str) -> float:
    begin, finish = time.get(start), time.get(end)
    if isinstance(begin, (int, float)) and isinstance(finish, (int, float)) and finish >= begin:
        return float(finish - begin)
    return 0.0


def union_ms(intervals: Iterable[tuple[float, float]]) -> float:
    """Length of the union of [start, end] intervals, so parallel tools count once."""
    total, current_start, current_end = 0.0, None, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None and current_start is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None and current_start is not None:
        total += current_end - current_start
    return total


def tool_interval(part: Mapping[str, Any]) -> tuple[float, float] | None:
    time = part.get("time") or {}
    start, end = time.get("ran", time.get("created")), time.get("completed")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return float(start), float(end)
    return None


@dataclass
class SessionTotals:
    turns: int = 0
    model_ms: float = 0.0
    tool_ms: float = 0.0


def session_totals(export: Mapping[str, Any]) -> SessionTotals:
    totals = SessionTotals()
    for message in assistant_messages(export):
        totals.turns += 1
        time = message.get("time") or {}
        totals.model_ms += _ms(time, "created", "streamed") or _ms(time, "created", "completed")
        intervals = [interval for part in tool_parts(message) if part.get("name") != SUBAGENT_TOOL
                     if (interval := tool_interval(part)) is not None]
        totals.tool_ms += union_ms(intervals)
    return totals


def _dispatches(exports: Iterable[Mapping[str, Any]]) -> tuple[list[JSON], int]:
    """New subagent dispatches in time order, and the follow-ups sent to a running child."""
    dispatches: list[tuple[float, JSON]] = []
    followups = 0
    for export in exports:
        for message in assistant_messages(export):
            for part in tool_parts(message):
                if part.get("name") != SUBAGENT_TOOL:
                    continue
                payload = (part.get("state") or {}).get("input") or {}
                if payload.get("sessionID"):
                    followups += 1
                    continue
                start = (part.get("time") or {}).get("created") or 0
                dispatches.append((float(start), dict(payload)))
    return [payload for _start, payload in sorted(dispatches, key=lambda item: item[0])], followups


def dispatch_metrics(exports: Iterable[Mapping[str, Any]]) -> JSON:
    dispatches, followups = _dispatches(exports)
    seen: Counter[str] = Counter()
    redispatched: list[str] = []
    for payload in dispatches:
        unit = unit_id(payload)
        if seen[unit]:
            redispatched.append(unit)
        seen[unit] += 1
    return {
        "subagent_dispatches": len(dispatches),
        "subagent_redispatches": len(redispatched),
        "subagent_followups": followups,
        "redispatched_units": sorted(set(redispatched)),
        "dispatches_by_agent": dict(sorted(Counter(str(p.get("agent", "?")) for p in dispatches).items())),
    }


def _token_metrics(messages: list[JSON], prices: Mapping[str, ModelPrice]) -> JSON:
    totals = Counter[str]()
    spend, reported = 0.0, 0.0
    by_model: dict[str, float] = {}
    unpriced: set[str] = set()
    for message in messages:
        tokens = Tokens.of(message)
        totals.update({"input_uncached": tokens.input, "output": tokens.output, "reasoning": tokens.reasoning,
                       "cache_read": tokens.cache_read, "cache_write": tokens.cache_write})
        key = model_key(message)
        reported += float(message.get("cost") or 0.0)
        if key not in prices:
            unpriced.add(key)
            continue
        cost = tokens.cost(prices[key])
        spend += cost
        by_model[key] = by_model.get(key, 0.0) + cost
    return {
        "input_tokens": totals["input_uncached"] + totals["cache_read"] + totals["cache_write"],
        "uncached_input_tokens": totals["input_uncached"],
        "cached_input_tokens": totals["cache_read"],
        "cache_write_tokens": totals["cache_write"],
        "output_tokens": totals["output"],
        "reasoning_tokens": totals["reasoning"],
        "spend_usd": round(spend, 6),
        "spend_by_model_usd": {key: round(value, 6) for key, value in sorted(by_model.items())},
        "opencode_reported_cost_usd": round(reported, 6),
        "unpriced_models": sorted(unpriced),
    }


def summarize(exports: Sequence[Mapping[str, Any]], lead_id: str, prices: Mapping[str, ModelPrice]) -> JSON:
    """Metrics over a lead session and every subagent session below it."""
    leads = [export for export in exports if (export.get("info") or {}).get("id") == lead_id]
    if len(leads) != 1:
        raise ValueError(f"exports hold {len(leads)} sessions with the lead id {lead_id}")
    messages = [message for export in exports for message in assistant_messages(export)]
    tools = Counter(str(part.get("name")) for message in messages for part in tool_parts(message))
    lead = session_totals(leads[0])
    children = [session_totals(export) for export in exports if export is not leads[0]]
    return {
        "model_turns": len(messages),
        "lead_model_turns": lead.turns,
        "subagent_model_turns": sum(child.turns for child in children),
        "subagent_sessions": len(children),
        "tool_calls": sum(tools.values()),
        "tool_calls_by_tool": dict(sorted(tools.items())),
        "failed_builds": sum(1 for message in messages for part in tool_parts(message) if is_failed_build(part)),
        "lead_model_time_s": round(lead.model_ms / 1000, 3),
        "lead_tool_time_s": round(lead.tool_ms / 1000, 3),
        "subagent_model_time_s": round(sum(child.model_ms for child in children) / 1000, 3),
        "subagent_tool_time_s": round(sum(child.tool_ms for child in children) / 1000, 3),
        **_token_metrics(messages, prices),
        **dispatch_metrics(exports),
    }


def child_session_ids(export: Mapping[str, Any]) -> list[str]:
    """Child session ids a session's subagent calls started, in call order."""
    ids: list[str] = []
    for message in assistant_messages(export):
        for part in tool_parts(message):
            if part.get("name") != SUBAGENT_TOOL:
                continue
            state = part.get("state") or {}
            child = (state.get("metadata") or {}).get("sessionID") or (state.get("input") or {}).get("sessionID")
            if isinstance(child, str) and child not in ids:
                ids.append(child)
    for message in export.get("messages") or ():
        child = (message.get("metadata") or {}).get("childID")
        if isinstance(child, str) and child not in ids:
            ids.append(child)
    return ids


def last_provider_error(export: Mapping[str, Any]) -> str | None:
    """The message of a provider error (`provider.auth`, `provider.*`) that ended the session's last turn, if any."""
    for message in reversed(list(export.get("messages") or ())):
        error = message.get("error")
        if isinstance(error, Mapping) and str(error.get("type", "")).startswith("provider."):
            return f"{error.get('type')}: {error.get('message', '')}"
        if message.get("type") == "user":
            return None
    return None


def last_assistant_text(export: Mapping[str, Any]) -> str:
    """Text of the lead's last reply since the last user message: the question or gate it stopped at."""
    messages = list(export.get("messages") or ())
    last_user = max((index for index, message in enumerate(messages) if message.get("type") == "user"), default=-1)
    for message in reversed(messages[last_user + 1:]):
        if message.get("type") != "assistant":
            continue
        text = "\n".join(str(part.get("text") or "") for part in message.get("content") or ()
                         if part.get("type") == "text").strip()
        if text:
            return text
    return ""
