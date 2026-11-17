"""Proof that a run's OpenCode loaded only the throwaway home, from OpenCode's own reporting.

The harness asks a server started in the run's environment, before any model call, for its
config sources (`config.get`), skills with their paths (`skill.list`), agents (`agent.list`)
and commands (`command.list`). `findings` lists every way the report shows something the
throwaway home did not supply; an empty list proves isolation. OpenCode loads skills
incrementally, so the caller polls until the installed skills are all listed and the list is
stable; a report missing an installed skill is itself a finding, never a pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

BUILTIN_SKILL_SCHEME = "skill://"
BUILTIN_AGENTS = frozenset({"build", "general", "explore", "compaction", "title", "summary", "plan"})
BUILTIN_COMMANDS = frozenset({"init", "review", "goal"})
OPERATIONS = ("config.get", "skill.list", "agent.list", "command.list")
# Package-download caches a run may share with the user (bench/run.py links the same names).
PACKAGE_CACHE_NAMES = ("uv", "pip", "ms-playwright", "cad-concept")
PACKAGE_CACHE_VARIABLES = ("UV_CACHE_DIR", "PIP_CACHE_DIR", "PLAYWRIGHT_BROWSERS_PATH")


@dataclass(frozen=True)
class Expected:
    """What the throwaway home installed, and the roots a loaded source may come from."""

    home: PurePath
    project: PurePath
    skills: frozenset[str]
    agents: frozenset[str]
    commands: frozenset[str]

    def allows(self, path: str) -> bool:
        candidate = PurePath(path)
        return any(candidate == root or root in candidate.parents for root in (self.home, self.project))


def items(report: Mapping[str, Any], operation: str) -> list[Any] | None:
    value = report.get(operation)
    if isinstance(value, Mapping):
        value = value.get("data")
    return list(value) if isinstance(value, list) else None


def _config_findings(entries: list[Any], expected: Expected) -> list[str]:
    found = [f"config source outside the throwaway home: {entry.get('path')}" for entry in entries
             if not isinstance(entry, Mapping) or not expected.allows(str(entry.get("path")))]
    own = str(expected.home / ".config/opencode/opencode.json")
    if not any(isinstance(entry, Mapping) and entry.get("path") == own for entry in entries):
        found.append(f"config sources do not include the throwaway config {own}")
    return found


def _skill_findings(skills: list[Any], expected: Expected) -> list[str]:
    found, names = [], set()
    for skill in skills:
        path = str(skill.get("path")) if isinstance(skill, Mapping) else ""
        names.add(str(skill.get("name")) if isinstance(skill, Mapping) else "")
        if not path.startswith(BUILTIN_SKILL_SCHEME) and not expected.allows(path):
            found.append(f"skill loaded from outside the throwaway home: {path}")
    found += [f"installed skill not loaded: {name}" for name in sorted(expected.skills - names)]
    return found


def _named_findings(items: list[Any], key: str, kind: str, allowed: frozenset[str], installed: frozenset[str]) -> list[str]:
    names = {str(item.get(key)) for item in items if isinstance(item, Mapping)}
    found = [f"{kind} the throwaway home did not supply: {name}" for name in sorted(names - allowed - installed)]
    found += [f"installed {kind} not loaded: {name}" for name in sorted(installed - names)]
    return found


def findings(report: Mapping[str, Any], expected: Expected) -> list[str]:
    """Every isolation problem the report shows; empty means the run read none of the user's config."""
    missing = [operation for operation in OPERATIONS if items(report, operation) is None]
    if missing:
        return [f"no {operation} report" for operation in missing]
    return [
        *_config_findings(items(report, "config.get") or [], expected),
        *_skill_findings(items(report, "skill.list") or [], expected),
        *_named_findings(items(report, "agent.list") or [], "id", "agent", BUILTIN_AGENTS, expected.agents),
        *_named_findings(items(report, "command.list") or [], "name", "command", BUILTIN_COMMANDS, expected.commands),
    ]


def cache_findings(opencode_cache: str | None, env: Mapping[str, str], home: PurePath) -> list[str]:
    """The effective cache directories must lie inside the run home: OpenCode's (from `opencode debug paths cache`)
    and XDG_CACHE_HOME, which the lead's tools (cad-flow's warm-up record) read."""
    def inside(path: str | None) -> bool:
        return path is not None and (PurePath(path) == home or home in PurePath(path).parents)

    found = []
    if opencode_cache is None:
        found.append("opencode reported no cache path")
    elif not inside(opencode_cache):
        found.append(f"opencode cache outside the run home: {opencode_cache}")
    if not inside(env.get("XDG_CACHE_HOME")):
        found.append(f"XDG_CACHE_HOME is not set inside the run home: {env.get('XDG_CACHE_HOME')}")
    return found


def private_cache_findings(cache: Path, allowed: Mapping[str, Path], home: PurePath) -> list[str]:
    """Each entry of the run's private cache is a link to an allowlisted package cache, or a real directory
    inside the run home; anything else would carry state between runs."""
    found = []
    for entry in sorted(cache.iterdir()) if cache.is_dir() else ():
        if entry.is_symlink():
            if entry.name not in allowed or entry.resolve() != Path(allowed[entry.name]).resolve():
                found.append(f"private cache entry {entry.name} links outside the allowlist: {entry.readlink()}")
        elif not entry.is_dir():
            found.append(f"private cache entry {entry.name} is not a directory")
        elif Path(home).resolve() not in entry.resolve().parents:
            found.append(f"private cache entry {entry.name} is outside the run home")
    return found


def package_cache_env_findings(env: Mapping[str, str], home: PurePath) -> list[str]:
    """A package-cache variable that points outside the run home must name an allowlisted package cache."""
    found = []
    for variable in PACKAGE_CACHE_VARIABLES:
        value = env.get(variable)
        if value and home not in PurePath(value).parents and PurePath(value).name not in PACKAGE_CACHE_NAMES:
            found.append(f"{variable} points outside the run home at a cache that is not allowlisted: {value}")
    return found


def skills_settled(previous: list[Any] | None, current: list[Any] | None, expected: Expected) -> bool:
    """True once every installed skill is listed and the listing did not change since the last poll."""
    if current is None or previous is None:
        return False
    names = {str(skill.get("name")) for skill in current if isinstance(skill, Mapping)}
    paths = sorted(str(skill.get("path")) for skill in current if isinstance(skill, Mapping))
    before = sorted(str(skill.get("path")) for skill in previous if isinstance(skill, Mapping))
    return expected.skills <= names and paths == before
