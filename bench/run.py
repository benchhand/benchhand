#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Run one agent-throughput benchmark run of the cad-flow lead on this machine.

LOCAL ONLY: this drives a paid model. Never run it in CI or on a shared host.

`run --case CASE --arm LABEL` copies the case fixture into a fresh scratch git project, installs
the repository's OpenCode harness into a throwaway OpenCode home, and drives the cad-flow lead
headless with `opencode run --format json --auto --archived`, continuing the same session. A
scripted user answers every question and gate from the case's answers.yaml. The run ends when the
mode's stop gate is answered (bench: PARTS; fixture: LAYOUT freeze), or at the turn, time or spend
cap, which counts as a failure. The harness then runs the hard checks, asks a judge model from
another vendor to score the rubric, writes one record under the results checkout, appends the
spend ledger, commits and pushes, and deletes the scratch directory.

`--mode fixture` builds a case fixture instead: it starts from an empty project (or `--seed-hw`),
stops at the LAYOUT freeze, and copies the frozen hw/ into bench/cases/<case>/fixture/.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import gzip
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import acceptance
import dialogue
import isolation
import metrics

JSON = dict[str, Any]

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
CASES = BENCH / "cases"
CONFIG = BENCH / "config.json"
TMP = Path("/private/var/folders/8d/02tkxcq55qzfyj7_z_ktft1r0000gq/T/opencode")
MARKER_ROOT = TMP / "cad-plan-c-bench"  # per-run pid, done and log; never a generic name in TMP itself
PUSH_LOCK = Path.home() / ".cache/cad-agent-push.lock"
RESULTS_BRANCH = "bench/results"
FIXTURE_EXCLUDE = (".cad", "__pycache__", ".DS_Store")
SCOPE = {
    "fixture": ("Scope for this session: take the design from GROUND through L0 CONCEPT and the VARIANT gate to "
                "L1 LAYOUT and its LAYOUT freeze gate. Stop once the LAYOUT freeze is decided."),
    "bench": ("The design record in hw/ is frozen at L1 LAYOUT. Scope for this session: continue from L1 through "
              "L2 PARTS and its PARTS gate. Stop once the PARTS gate is decided."),
}
TARGET_STAGE = {"fixture": "layout", "bench": "parts"}
SPEND_POLL_S = 20.0
SETTLED_POLLS = 3
MONITOR_POLL_S = 30.0


class BenchError(RuntimeError):
    """The run cannot start or continue."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Paths:
    run_id: str
    scratch: Path
    project: Path
    home: Path
    raw: Path
    results: Path
    run_dir: Path

    @property
    def skills(self) -> Path:
        return self.home / ".config/opencode/skills"

    @property
    def db(self) -> Path:
        return self.home / ".local/share/opencode/opencode.db"

    @classmethod
    def create(cls, run_id: str, results: Path) -> Paths:
        scratch = TMP / f"bench-{run_id}"
        scratch.mkdir(parents=False, exist_ok=False)
        paths = cls(run_id, scratch, scratch / "project", scratch / "home", scratch / "raw", results,
                    results / "runs" / run_id)
        for folder in (paths.project, paths.home, paths.raw):
            folder.mkdir()
        paths.run_dir.mkdir(parents=True, exist_ok=False)
        return paths


# --- environment -------------------------------------------------------------------------------

def real_base(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / default)


XDG = (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state"))


def link_farm(home: Path) -> None:
    """Point every non-OpenCode XDG entry of the throwaway home at the user's real one.

    OpenCode gets a private config, data and state directory, while git, uv and mise in the
    lead's shell keep reading the user's own configuration through these links.
    """
    for variable, default in XDG:
        real, fake = real_base(variable, default), home / default
        fake.mkdir(parents=True, exist_ok=True)
        if not real.is_dir():
            continue
        for entry in sorted(real.iterdir()):
            if entry.name != "opencode" and not (fake / entry.name).exists():
                (fake / entry.name).symlink_to(entry)


def opencode_env(paths: Paths) -> dict[str, str]:
    env = dict(os.environ)
    env["PWD"] = str(paths.project)  # opencode takes its working directory from PWD, not the process cwd
    for variable, default in XDG:
        env[variable] = str(paths.home / default)
    env.update({
        "OPENCODE_TEST_HOME": str(paths.home),  # opencode's own home lookups (~/.agents, ~/.claude skills)
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    })
    return env


def install_harness(paths: Paths, config: Mapping[str, Any]) -> None:
    specs = [f"opencode:{tier}={model}" for tier, model in sorted((config.get("tier_models") or {}).items())]
    command = [sys.executable, str(ROOT / "harness/install.py"), "--harness", "opencode", "--user"]
    for spec in specs:
        command += ["--tier-model", spec]
    result = subprocess.run(command, env={**os.environ, "HOME": str(paths.home)}, capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise BenchError(f"harness install failed: {result.stderr.strip()}")
    link_farm(paths.home)
    settings = dict(config["opencode"])
    settings["model"] = config["producer"]["model"]
    (paths.home / ".config/opencode/opencode.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def expected_install(paths: Paths) -> isolation.Expected:
    """The skills, agents and commands the harness installed into the throwaway home."""
    scope = paths.home / ".config/opencode"
    return isolation.Expected(
        home=paths.home, project=paths.project,
        skills=frozenset(p.parent.name for p in (scope / "skills").glob("*/SKILL.md")),
        agents=frozenset(p.stem for p in (scope / "agents").glob("*.md")),
        commands=frozenset(p.stem for p in (scope / "commands").glob("*.md")),
    )


@contextlib.contextmanager
def opencode_server(paths: Paths, env: Mapping[str, str], timeout: float) -> Iterator[tuple[str, str]]:
    """A local OpenCode server in the run's environment; yields its URL and password (never written anywhere)."""
    process = subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", "0"], cwd=paths.project,
                               env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               text=True, start_new_session=True)
    found: dict[str, str] = {}

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            for key, prefix in (("url", "server listening on "), ("password", "server password ")):
                if line.startswith(prefix):
                    found[key] = line[len(prefix):].strip()

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + timeout
    try:
        while len(found) < 2 and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.2)
        if len(found) < 2:
            raise BenchError("isolation check: opencode serve printed no URL and password")
        yield found["url"], found["password"]
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)


def api_call(server: tuple[str, str], env: Mapping[str, str], project: Path, operation: str) -> Any:
    url, password = server
    command = ["opencode", "api", "--server", url, operation, "--param", f"location[directory]={project}"]
    result = subprocess.run(command, cwd=project, env={**env, "OPENCODE_SERVER_PASSWORD": password}, capture_output=True,
                            text=True, timeout=60, check=False)
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(result.stdout)
    return None


def summarize_report(report: Mapping[str, Any]) -> JSON:
    """The report without skill and agent bodies: sources, names and paths only."""
    def data(operation: str) -> list[Any]:
        value = report.get(operation)
        value = value.get("data") if isinstance(value, Mapping) else value
        return value if isinstance(value, list) else []

    return {
        "config_sources": [entry.get("path") for entry in data("config.get") if isinstance(entry, Mapping)],
        "skills": sorted(str(item.get("path")) for item in data("skill.list") if isinstance(item, Mapping)),
        "agents": sorted(str(item.get("id")) for item in data("agent.list") if isinstance(item, Mapping)),
        "commands": sorted(str(item.get("name")) for item in data("command.list") if isinstance(item, Mapping)),
    }


def check_isolation(paths: Paths, env: Mapping[str, str], timeout: float = 120.0) -> JSON:
    """Ask OpenCode itself what it loaded in the run's environment, before any model call."""
    expected = expected_install(paths)
    report: JSON = {}
    with opencode_server(paths, env, timeout) as server:
        # OpenCode loads skill sources one by one: wait until every installed skill is listed and the
        # listing held still for SETTLED_POLLS polls, so a source that loads late is still seen.
        deadline, previous, settled = time.monotonic() + timeout, None, 0
        while time.monotonic() < deadline and settled < SETTLED_POLLS:
            report["skill.list"] = api_call(server, env, paths.project, "skill.list")
            current = isolation.items(report, "skill.list")
            settled = settled + 1 if isolation.skills_settled(previous, current, expected) else 0
            previous = current
            time.sleep(1.0)
        for operation in ("config.get", "agent.list", "command.list"):
            report[operation] = api_call(server, env, paths.project, operation)
    found = isolation.findings(report, expected)
    return {"passed": not found, "findings": found, "loaded": summarize_report(report)}


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


def prepare_project(paths: Paths, case_dir: Path, mode: str, seed: Path | None) -> None:
    source = seed if mode == "fixture" else case_dir / "fixture/hw"
    if source is not None:
        if not (source / "product.yaml").is_file():
            raise BenchError(f"no hw/ record at {source}")
        shutil.copytree(source, paths.project / "hw", ignore=shutil.ignore_patterns(*FIXTURE_EXCLUDE), symlinks=True)
    git(paths.project, "init", "-q", "-b", "main")
    git(paths.project, "add", "-A")
    git(paths.project, "commit", "-q", "--allow-empty", "-m", f"bench {paths.run_id}: start")


def load_answers(case_dir: Path) -> dialogue.Script:
    case = load_yaml(case_dir / "answers.yaml") or {}
    if case.get("extends"):
        case = dialogue.merge(case, load_yaml(CASES / str(case["extends"]) / "answers.yaml") or {})
    return dialogue.load_script(case)


def first_message(paths: Paths, case_dir: Path, mode: str) -> str:
    template = (paths.home / ".config/opencode/commands/cad-flow.md").read_text(encoding="utf-8")
    body = template.split("---", 2)[2].strip() if template.startswith("---") else template.strip()
    brief = (case_dir / "brief.md").read_text(encoding="utf-8").strip()
    return body.replace("$ARGUMENTS", f"\n\n{brief}\n\n{SCOPE[mode]}\n")


# --- process control ---------------------------------------------------------------------------

def stop_group(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def scratch_processes(scratch: Path) -> list[int]:
    """Processes whose command line or working directory is inside the scratch directory."""
    mine, prefix = os.getpid(), str(scratch)
    pids: set[int] = set()
    listing = subprocess.run(["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True, check=False)
    for line in listing.stdout.splitlines():
        number, _, command = line.strip().partition(" ")
        if prefix in command and number.isdigit():
            pids.add(int(number))
    cwd = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn"], capture_output=True, text=True, check=False)
    owner: int | None = None
    for line in cwd.stdout.splitlines():
        if line.startswith("p"):
            owner = int(line[1:])
        elif line.startswith("n") and line[1:].startswith(prefix) and owner is not None:
            pids.add(owner)
    return sorted(pids - {mine})


def kill_leftovers(scratch: Path) -> list[int]:
    pids = scratch_processes(scratch)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)
        time.sleep(3)
    return pids


def db_cost(db: Path) -> float:
    """OpenCode's own running cost over every session of the run (models.dev prices)."""
    if not db.exists():
        return 0.0
    try:
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as connection:
            row = connection.execute("select coalesce(sum(cost), 0) from session_v2").fetchone()
    except sqlite3.Error:
        return 0.0
    return float(row[0] or 0.0)


@dataclass
class Monitor:
    """Peak load and lowest available memory while the run is active."""

    peak_load1: float = 0.0
    min_available_gb: float | None = None
    _stop: threading.Event = field(default_factory=threading.Event)

    @staticmethod
    def available_gb() -> float | None:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False)
        lines = result.stdout.splitlines()
        if not lines or "page size of" not in lines[0]:
            return None
        page = int(lines[0].split("page size of")[1].split()[0])
        pages = 0
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.strip() in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
                pages += int(value.strip().rstrip("."))
        return pages * page / 1e9

    def sample(self) -> None:
        self.peak_load1 = max(self.peak_load1, os.getloadavg()[0])
        available = self.available_gb()
        if available is not None:
            self.min_available_gb = available if self.min_available_gb is None else min(self.min_available_gb, available)

    def run(self) -> None:
        while not self._stop.wait(MONITOR_POLL_S):
            self.sample()

    @contextlib.contextmanager
    def active(self) -> Iterator[None]:
        thread = threading.Thread(target=self.run, daemon=True)
        self.sample()
        thread.start()
        try:
            yield
        finally:
            self._stop.set()
            thread.join(timeout=5)

    def to_json(self) -> JSON:
        return {"peak_load1": round(self.peak_load1, 2), "min_available_gb":
                None if self.min_available_gb is None else round(self.min_available_gb, 2)}


# --- driving the lead --------------------------------------------------------------------------

@dataclass
class Invocation:
    turn: int
    seconds: float
    exit_code: int | None
    killed: str | None
    session: str | None


@dataclass
class Driver:
    paths: Paths
    env: dict[str, str]
    model: str | None
    agent: str | None
    spend_cap: float
    session: str | None = None
    invocations: list[Invocation] = field(default_factory=list)

    def command(self, message: str) -> list[str]:
        command = ["opencode", "run", "--standalone", "--format", "json", "--auto", "--archived",
                   "--title", f"bench {self.paths.run_id}"]
        if self.session:
            command += ["--session", self.session]
        if self.model:
            command += ["--model", self.model]
        if self.agent:
            command += ["--agent", self.agent]
        # A message that starts with "-" would parse as a flag.
        return [*command, message if not message.startswith("-") else f" {message}"]

    def _watch(self, process: subprocess.Popen[bytes], deadline: float) -> str | None:
        """Wait for the process; stop it at the time or spend cap and return which cap it hit."""
        while True:
            try:
                process.wait(timeout=SPEND_POLL_S)
                return None
            except subprocess.TimeoutExpired:
                pass
            if db_cost(self.paths.db) > self.spend_cap:
                stop_group(process)
                return "spend"
            if time.monotonic() > deadline:
                stop_group(process)
                return "time"

    def send(self, message: str, deadline: float) -> Invocation:
        turn = len(self.invocations) + 1
        events = self.paths.raw / f"turn-{turn:03d}.jsonl"
        started = time.monotonic()
        with events.open("wb") as stdout, (self.paths.raw / f"turn-{turn:03d}.stderr").open("wb") as stderr:
            process = subprocess.Popen(self.command(message), cwd=self.paths.project, env=self.env, stdout=stdout,
                                       stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
            killed = self._watch(process, deadline)
        if self.session is None:
            self.session = first_session_id(events)
        invocation = Invocation(turn, time.monotonic() - started, process.returncode, killed, self.session)
        self.invocations.append(invocation)
        return invocation

    def export(self, session: str, sanitize: bool = False) -> JSON:
        command = ["opencode", "session", "export", "--standalone", session, *(["--sanitize"] if sanitize else [])]
        result = subprocess.run(command, cwd=self.paths.project, env=self.env, capture_output=True, check=False)
        if result.returncode != 0:
            raise BenchError(f"export of {session} failed: {result.stderr.decode(errors='replace').strip()}")
        data: JSON = json.loads(result.stdout)
        return data


def first_session_id(events: Path) -> str | None:
    for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            session = json.loads(line).get("sessionID")
            if isinstance(session, str):
                return session
    return None


@dataclass
class Conversation:
    outcome: str = "running"
    entries: list[JSON] = field(default_factory=list)


def record_turn(conversation: Conversation, invocation: Invocation, text: str, reply: dialogue.Reply | None,
                sent: bool = True) -> None:
    entry: JSON = {"turn": invocation.turn, "seconds": round(invocation.seconds, 1), "exit_code": invocation.exit_code,
                   "killed": invocation.killed, "lead_text": text}
    if reply is not None:
        entry.update({"reply_kind": reply.kind, "reply": reply.message, "sent": sent, "wait_s": reply.wait_s,
                      "stops": reply.stops, "at_gate": reply.at_gate,
                      "asks": [{"text": ask.text, "intent": ask.intent, "kind": ask.kind, "matched": ask.matched,
                                "profile": ask.profile} for ask in reply.asks]})
    conversation.entries.append(entry)


def terminal_outcome(stopping: bool, failures: int, turns: int, caps: Mapping[str, Any], reply: dialogue.Reply,
                     self_approved: bool = False) -> str | None:
    """Why the run ends after this turn, or None to send the reply.

    `self_approved` means the record already holds an approval of the stop gate the scripted user never
    gave: the lead closed a human gate on its own, so the run fails rather than run on past its scope.
    """
    if stopping or failures >= 2:
        return "reached" if stopping and not failures else "error"
    if self_approved:
        return "fail:gate-approved-unasked"
    if reply.unmatched_at_gate:
        return "fail:unmatched-at-gate"
    if turns >= int(caps["max_turns"]):
        return "cap:turns"
    return None


def converse(driver: Driver, script: dialogue.Script, mode: str, opening: str, caps: Mapping[str, Any]) -> Conversation:
    """Send the opening, then answer the lead until the stop gate is answered, a cap is hit, or a gate asks
    something the script cannot answer. Every lead reply is classified, the last one too."""
    conversation = Conversation()
    deadline = time.monotonic() + float(caps["max_active_minutes"]) * 60
    message, stopping, failures = opening, False, 0
    while True:
        invocation = driver.send(message, deadline)
        if invocation.killed:
            conversation.outcome = f"cap:{invocation.killed}"
            record_turn(conversation, invocation, "", None)
            return conversation
        if driver.session is None:
            raise BenchError("opencode run printed no session id; see the raw turn files")
        text = metrics.last_assistant_text(driver.export(driver.session))
        failures = failures + 1 if invocation.exit_code else 0
        hw = driver.paths.project / "hw"
        can_stop = acceptance.latest_at_stage(built_versions(hw), TARGET_STAGE[mode]) is not None
        reply = dialogue.respond(script, text, mode, can_stop)
        self_approved = not stopping and bool(acceptance.stop_gate_approvals(decisions(hw), mode))
        outcome = terminal_outcome(stopping, failures, len(driver.invocations), caps, reply, self_approved)
        record_turn(conversation, invocation, text, reply, sent=outcome is None)
        if outcome is not None:
            conversation.outcome = outcome
            return conversation
        message, stopping = reply.message, reply.stops


def export_tree(driver: Driver, lead: str, sanitize: bool) -> list[JSON]:
    exports: list[JSON] = []
    queue, seen = [lead], set()
    while queue:
        session = queue.pop(0)
        if session in seen:
            continue
        seen.add(session)
        raw = driver.export(session)
        exports.append(driver.export(session, sanitize=True) if sanitize else raw)
        queue.extend(metrics.child_session_ids(raw))
    return exports


def unmatched_questions(conversation: Conversation) -> list[JSON]:
    """Every question no intent matched, with its turn and whether the lead asked it at a gate."""
    return [{"turn": entry["turn"], "text": ask["text"], "at_gate": entry["at_gate"]}
            for entry in conversation.entries if "asks" in entry for ask in entry["asks"] if not ask["matched"]]


def dialogue_metrics(conversation: Conversation, driver: Driver) -> JSON:
    """Question and wait counts. Every lead reply's asks count; only replies actually sent count as waits."""
    classified = [entry for entry in conversation.entries if "reply_kind" in entry]
    sent = [entry for entry in classified if entry["sent"]]
    asks = [ask for entry in classified for ask in entry["asks"]]
    waits = [entry for entry in sent if entry["reply_kind"] in ("question", "gate")]
    return {
        "harness_turns": len(driver.invocations),
        "active_wall_s": round(sum(invocation.seconds for invocation in driver.invocations), 1),
        "human_wait_events": len(waits),
        "human_wait_s": sum(float(entry["wait_s"]) for entry in waits),
        "nudges": sum(1 for entry in sent if entry["reply_kind"] == "nudge"),
        "questions_asked": len(asks),
        "questions_matched": sum(1 for ask in asks if ask["matched"]),
        "questions_unmatched": sum(1 for ask in asks if not ask["matched"]),
        "questions_unmatched_at_gate": sum(1 for entry in classified if entry["at_gate"]
                                           for ask in entry["asks"] if not ask["matched"]),
        "gates_answered": sum(1 for entry in sent for ask in entry["asks"] if ask["kind"] == "gate"),
        "profile_questions_asked": sum(1 for ask in asks if ask["profile"]),
        "profile_asked": any(ask["profile"] for ask in asks),
    }


# --- hard checks -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tool:
    paths: Paths
    out: Path

    def run(self, name: str, script: str, args: Sequence[str], timeout: float) -> tuple[int | None, str]:
        command = ["uv", "run", "--script", str(self.paths.skills / "cad-flow/scripts/run.py"), script, *args]
        try:
            result = subprocess.run(command, cwd=self.paths.project, env={**os.environ, "PWD": str(self.paths.project)},
                                    capture_output=True, text=True, timeout=timeout, check=False, start_new_session=True)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as error:
            code, stdout = None, str(error.stdout or "")
            stderr = f"timed out after {timeout} s"
        (self.out / f"{name}.stdout.json").write_text(stdout, encoding="utf-8")
        (self.out / f"{name}.stderr.txt").write_text(stderr[-20000:], encoding="utf-8")
        return code, stdout


def built_versions(hw: Path) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for folder in sorted((hw / "versions").glob("v*")):
        snapshot = folder / "record/product.yaml"
        if (folder / "manifest.json").is_file() and snapshot.is_file():
            product = load_yaml(snapshot) or {}
            versions[folder.name] = ((product.get("flow") or {}).get("stage")) if isinstance(product, dict) else None
    return versions


def parse_json(text: str) -> Any:
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(text)
    start = text.find("{")
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(text[start:]) if start >= 0 else None
    return None


def decisions(hw: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8", errors="replace") for path in sorted((hw / "decisions").glob("DR-*.md"))}


def layout_frozen(hw: Path) -> acceptance.CheckResult:
    product = (load_yaml(hw / "product.yaml") if (hw / "product.yaml").is_file() else None) or {}
    stage = (product.get("flow") or {}).get("stage") if isinstance(product, dict) else None
    approved = acceptance.stop_gate_approvals(decisions(hw), "fixture")
    passed = stage not in (None, "ground", "intent", "specify", "concept", "layout") and bool(approved)
    return acceptance.CheckResult("layout_frozen", passed, f"flow.stage {stage}; approved LAYOUT freeze DRs {approved}")


def hard_checks(paths: Paths, mode: str, rubric: acceptance.Rubric, timeouts: Mapping[str, float]) -> tuple[JSON, str | None]:
    out = paths.run_dir / "checks"
    out.mkdir(exist_ok=True)
    tool, hw = Tool(paths, out), paths.project / "hw"
    stage = TARGET_STAGE[mode]
    label = acceptance.latest_at_stage(built_versions(hw), stage)
    results = [acceptance.CheckResult(f"{acceptance.STAGE_LEVEL[stage].lower()}_version", label is not None,
                                      f"latest {stage} version: {label}")]
    code, _ = tool.run("validate", "cad-record/scripts/validate.py", [str(paths.project)], timeouts["default"])
    results.append(acceptance.CheckResult("validate", code == 0, f"validate.py exit {code}", code))
    if mode == "fixture":
        results.append(layout_frozen(hw))
    if label is not None:
        code, _ = tool.run("cad_model_check", "cad-model/scripts/cad_model.py", ["check", label, "--hw", "hw"],
                           timeouts["default"])
        results.append(acceptance.CheckResult("cad_model_check", code == 0, f"cad_model check {label} exit {code}", code))
        if mode == "bench":
            code, stdout = tool.run("form_check", "cad-design/scripts/form_check.py",
                                    ["--hw", "hw", "--version", label, "--fidelity", "F2"], timeouts["default"])
            results += acceptance.form_results(parse_json(stdout), code)
        if rubric.sweep:
            code, _ = tool.run("sweep", "cad-motion/scripts/sweep.py", ["--hw", "hw", "--version", label],
                               timeouts["sweep"])
            results.append(acceptance.CheckResult("sweep", code == 0, f"sweep {label} exit {code}", code))
    return acceptance.hard_verdict(results), label


# --- judge -------------------------------------------------------------------------------------

RECORD_SUFFIXES = (".yaml", ".csv", ".md")
RECORD_CHAR_LIMIT = 250_000


def judge_record(hw: Path, label: str) -> dict[str, str]:
    """The version's record snapshot plus the live BOM, verification and decisions, as text."""
    files: dict[str, str] = {}
    snapshot = hw / "versions" / label / "record"
    sources = [*sorted(snapshot.rglob("*")), hw / "bom.csv", hw / "verification.csv", *sorted((hw / "decisions").glob("*.md"))]
    total = 0
    for path in sources:
        if not path.is_file() or path.suffix not in RECORD_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if total + len(text) > RECORD_CHAR_LIMIT:
            files[str(path.relative_to(hw))] = "[omitted: record text limit reached]"
            continue
        total += len(text)
        files[str(path.relative_to(hw))] = text
    return files


def judge_renders(paths: Paths, label: str, timeout: float) -> list[Path]:
    renders = sorted((paths.project / "hw/renders" / label).glob("*/*.png"))
    if not renders:
        Tool(paths, paths.run_dir / "checks").run("render_for_judge", "cad-render/scripts/render.py",
                                                    ["hw", "--version", label], timeout)
        renders = sorted((paths.project / "hw/renders" / label).glob("*/*.png"))
    return renders


def check_summary(hard: Mapping[str, Any], out: Path) -> JSON:
    summary: JSON = {"hard_checks": hard["checks"]}
    for path in sorted(out.glob("*.stdout.json")):
        text = path.read_text(encoding="utf-8")
        summary[path.name.removesuffix(".stdout.json")] = text if len(text) < 40_000 else text[:40_000] + "\n[truncated]"
    return summary


def run_judge(driver: Driver, config: Mapping[str, Any], rubric: acceptance.Rubric, hard: Mapping[str, Any],
              label: str, prices: Mapping[str, metrics.ModelPrice]) -> JSON:
    paths = driver.paths
    folder = paths.scratch / "judge"
    (folder / ".opencode").mkdir(parents=True)
    deny_all = {"$schema": "https://opencode.ai/config.json",
                "permissions": [{"action": "*", "resource": "*", "effect": "deny"}]}
    (folder / ".opencode/opencode.json").write_text(json.dumps(deny_all) + "\n", encoding="utf-8")
    renders = judge_renders(paths, label, float(config["check_timeouts_s"]["default"]))
    prompt = acceptance.judge_prompt(rubric, judge_record(paths.project / "hw", label),
                                     check_summary(hard, paths.run_dir / "checks"), [p.name for p in renders])
    command = ["opencode", "run", "--standalone", "--format", "json", "--archived", "--title", f"judge {paths.run_id}",
               "--model", config["judge"]["model"]]
    for render in renders:
        command += ["--file", str(render)]
    events = paths.raw / "judge.jsonl"
    with events.open("wb") as stdout:
        subprocess.run([*command, prompt], cwd=folder, env={**driver.env, "PWD": str(folder)}, stdout=stdout, stderr=subprocess.DEVNULL,
                       check=False, timeout=1800)
    session = first_session_id(events)
    if session is None:
        return {"model": config["judge"]["model"], "error": "judge printed no session", "spend_usd": 0.0}
    export = driver.export(session)
    text = metrics.last_assistant_text(export)
    spend = metrics.summarize([export], session, prices)["spend_usd"]
    result: JSON = {"model": config["judge"]["model"], "renders": [str(p) for p in renders], "spend_usd": spend,
                    "reply": text}
    try:
        result["verdicts"] = acceptance.parse_verdicts(text, rubric)
    except acceptance.RubricError as error:
        result["error"] = str(error)
    return result


# --- results -----------------------------------------------------------------------------------

@contextlib.contextmanager
def push_lock() -> Iterator[None]:
    PUSH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with PUSH_LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_gz(path: Path, data: Any) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(data, stream)


def ledger_total(results: Path) -> float:
    ledger = results / "spend.jsonl"
    if not ledger.exists():
        return 0.0
    return sum(float(json.loads(line).get("total_usd") or 0.0)
               for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())


def publish(paths: Paths, record: Mapping[str, Any], phase: str, push: bool) -> float:
    """Append the ledger and commit under the push lock, then push with the documented command."""
    entry = {"run_id": paths.run_id, "phase": phase, "case": record["case"], "arm": record["arm"],
             "mode": record["mode"], "outcome": record["outcome"], **record["spend"], "finished": record["finished"]}
    with push_lock():
        with (paths.results / "spend.jsonl").open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(entry) + "\n")
        total = ledger_total(paths.results)
        (paths.results / "spend-total.json").write_text(json.dumps({"total_usd": round(total, 4), "updated": now()},
                                                                   indent=2) + "\n", encoding="utf-8")
        own = str(paths.run_dir.relative_to(paths.results))  # never another running run's partial files
        subprocess.run(["git", "-C", str(paths.results), "add", "-A", "--", own, "spend.jsonl", "spend-total.json"],
                       check=True)
        message = f"bench: {paths.run_id} {record['outcome']} ${record['spend']['total_usd']:.2f} (total ${total:.2f})"
        subprocess.run(["git", "-C", str(paths.results), "commit", "-q", "-m", message], check=True)
    if push:
        subprocess.run(["lockf", "-k", str(PUSH_LOCK), "dgit", "-C", str(paths.results), "push", "origin",
                        f"HEAD:{RESULTS_BRANCH}"], check=True)
    return total


def freeze_fixture(paths: Paths, case_dir: Path) -> Path:
    target = case_dir / "fixture/hw"
    if target.exists():
        raise BenchError(f"fixture exists, not overwritten: {target}")
    shutil.copytree(paths.project / "hw", target, ignore=shutil.ignore_patterns(*FIXTURE_EXCLUDE), symlinks=True)
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    return target


def remove_scratch(paths: Paths) -> None:
    """Delete exactly this run's scratch directory, read-only part-store folders included.

    Only real directories inside it get write permission back; the link farm's symlinks into the
    user's own directories are unlinked, never followed.
    """
    if paths.scratch.parent != TMP or not paths.scratch.name.startswith("bench-"):
        raise BenchError(f"refusing to delete {paths.scratch}")
    for current, dirs, _files in os.walk(paths.scratch, followlinks=False):
        for name in dirs:
            folder = os.path.join(current, name)
            if not os.path.islink(folder):
                os.chmod(folder, 0o755)
    shutil.rmtree(paths.scratch)


# --- main --------------------------------------------------------------------------------------

def check_results_checkout(results: Path) -> None:
    branch = subprocess.run(["git", "-C", str(results), "branch", "--show-current"], capture_output=True, text=True,
                            check=False).stdout.strip()
    if branch != RESULTS_BRANCH:
        raise BenchError(f"{results} is not a checkout of {RESULTS_BRANCH} (branch {branch!r})")


def evaluate(driver: Driver, config: Mapping[str, Any], mode: str, rubric: acceptance.Rubric,
             prices: Mapping[str, metrics.ModelPrice], judge: bool) -> JSON:
    hard, label = hard_checks(driver.paths, mode, rubric, config["check_timeouts_s"])
    verdicts, judged = None, None
    if judge and label is not None:
        judged = run_judge(driver, config, rubric, hard, label, prices)
        (driver.paths.run_dir / "judge.json").write_text(json.dumps(judged, indent=2) + "\n", encoding="utf-8")
        verdicts = judged.get("verdicts")
    return {"version": label, "hard": hard, "judge": judged, "verdicts": verdicts,
            "accepted": acceptance.accepted(hard, verdicts, rubric) if judge else None}


@dataclass(frozen=True)
class Case:
    name: str
    folder: Path
    script: dialogue.Script
    rubric: acceptance.Rubric


def base_record(args: argparse.Namespace, config: Mapping[str, Any], paths: Paths, started: str, outcome: str,
                isolation_result: Mapping[str, Any]) -> JSON:
    return {
        "run_id": paths.run_id, "case": args.case, "arm": args.arm, "mode": args.mode, "started": started,
        "finished": now(), "producer_model": args.model or config["producer"]["model"],
        "agent": args.agent or config["producer"].get("agent") or "build", "judge_model": config["judge"]["model"],
        "outcome": outcome, "accepted": False if args.mode == "bench" else None, "isolation": dict(isolation_result),
        "repo_commit": repo_commit(),
        "spend": {"producer_usd": 0.0, "judge_usd": 0.0, "total_usd": 0.0, "opencode_reported_usd": 0.0,
                  "currency": config["currency"]},
    }


def run_session(args: argparse.Namespace, config: Mapping[str, Any], case: Case, paths: Paths, env: dict[str, str],
                caps: Mapping[str, Any], record: JSON) -> JSON:
    """Drive the lead, evaluate the result and fill the run record."""
    prices = metrics.load_prices(config["prices_usd_per_million"])
    driver = Driver(paths, env, record["producer_model"], args.agent or config["producer"].get("agent"),
                    float(args.spend_cap or caps["run_spend_usd"]))
    monitor = Monitor()
    with monitor.active():
        conversation = converse(driver, case.script, args.mode, first_message(paths, case.folder, args.mode), caps)
        leftovers = kill_leftovers(paths.scratch)
        evaluation = evaluate(driver, config, args.mode, case.rubric, prices, judge=args.mode == "bench" and not args.no_judge)
    exports = export_tree(driver, driver.session or "", sanitize=False)
    run_metrics = {**metrics.summarize(exports, driver.session or "", prices), **dialogue_metrics(conversation, driver)}
    sessions = paths.run_dir / "sessions"
    sessions.mkdir()
    for export in export_tree(driver, driver.session or "", sanitize=True):
        write_gz(sessions / f"{export['info']['id']}.json.gz", export)
    (paths.run_dir / "dialogue.json").write_text(json.dumps(conversation.entries, indent=2) + "\n", encoding="utf-8")
    judge_spend = float((evaluation["judge"] or {}).get("spend_usd") or 0.0)
    accepted = evaluation["accepted"]
    record.update({
        "finished": now(), "outcome": conversation.outcome,
        # A cap, an error or an unmatched question at a gate fails the run whatever the checks say.
        "accepted": None if accepted is None else accepted and conversation.outcome == "reached",
        "version": evaluation["version"], "metrics": run_metrics, "unmatched_questions": unmatched_questions(conversation),
        "hard_checks": evaluation["hard"], "verdicts": evaluation["verdicts"], "machine": monitor.to_json(),
        "leftover_processes_killed": len(leftovers),
        "spend": {"producer_usd": run_metrics["spend_usd"], "judge_usd": round(judge_spend, 6),
                  "total_usd": round(run_metrics["spend_usd"] + judge_spend, 6),
                  "opencode_reported_usd": round(db_cost(paths.db), 6), "currency": config["currency"]},
    })
    if args.mode == "fixture" and conversation.outcome == "reached":
        record["fixture"] = str(freeze_fixture(paths, case.folder))
    return record


def execute(args: argparse.Namespace, config: Mapping[str, Any]) -> int:
    folder = CASES / args.case
    case = Case(args.case, folder, load_answers(folder), acceptance.load_rubric(load_yaml(folder / "requirements.yaml")))
    check_results_checkout(args.results)
    if args.budget is not None and ledger_total(args.results) >= args.budget:
        raise BenchError(f"ledger total reached the budget ${args.budget}")
    run_id = args.run_id
    paths = Paths.create(run_id, args.results)
    caps = {**config["caps"], **{key: value for key, value in (("max_turns", args.max_turns),
                                                               ("max_active_minutes", args.max_active_minutes))
                                  if value is not None}}
    started = now()
    try:
        prepare_project(paths, folder, args.mode, args.seed_hw)
        install_harness(paths, config)
        env = opencode_env(paths)
        isolated = check_isolation(paths, env)  # before any model call
        record = base_record(args, config, paths, started, "running" if isolated["passed"] else "isolation_failed", isolated)
        if isolated["passed"]:
            record = run_session(args, config, case, paths, env, caps, record)
        (paths.run_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        total = publish(paths, record, args.phase, push=not args.no_push)
    except BaseException:
        print(f"run {run_id} failed; scratch kept at {paths.scratch}", file=sys.stderr)
        raise
    print(json.dumps({"run_id": run_id, "outcome": record["outcome"], "accepted": record["accepted"],
                      "spend_usd": record["spend"]["total_usd"], "ledger_total_usd": round(total, 4),
                      "record": str(paths.run_dir / "record.json"), "fixture": record.get("fixture"),
                      "isolation_findings": record["isolation"]["findings"]}, indent=2))
    if not args.keep_scratch:
        kill_leftovers(paths.scratch)
        remove_scratch(paths)
    if record["outcome"] == "isolation_failed":
        return 4
    over = record["spend"]["total_usd"] > float(caps["run_spend_usd"]) or record["outcome"] == "cap:spend"
    return 3 if over else 0


def repo_commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=False).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", required=True, choices=sorted(p.name for p in CASES.iterdir() if p.is_dir() and not p.name.startswith("_")))
    parser.add_argument("--arm", required=True, help="label of the configuration under test, e.g. baseline")
    parser.add_argument("--mode", choices=dialogue.MODES, default="bench")
    parser.add_argument("--results", type=Path, required=True, help=f"checkout of the {RESULTS_BRANCH} branch")
    parser.add_argument("--phase", default="pilot", help="ledger phase label (fixtures, pilot, baseline)")
    parser.add_argument("--model", help="producer model provider/model[#variant] (default from config)")
    parser.add_argument("--agent", help="primary agent for the lead (default build)")
    parser.add_argument("--seed-hw", type=Path, help="fixture mode: start from this hw/ record instead of an empty project")
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--max-active-minutes", type=float)
    parser.add_argument("--spend-cap", type=float, help="USD; stop the run above this (default config caps.run_spend_usd)")
    parser.add_argument("--budget", type=float, help="USD; refuse to start once the ledger total reaches it")
    parser.add_argument("--run-id")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        print("FAIL: bench/run.py drives a paid model and runs only on a developer machine, never in CI", file=sys.stderr)
        return 2
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    args.run_id = args.run_id or f"{args.case}-{args.arm}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
    try:
        markers = claim_markers(args.run_id)
    except BenchError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    code = 1
    try:
        code = execute(args, config)
    except BenchError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        code = 1
    finally:
        (markers / "done").write_text(f"{code}\n", encoding="utf-8")
    return code


def claim_markers(run_id: str) -> Path:
    """This run's own marker directory, TMP/cad-plan-c-bench/<run-id>/: `pid` now, `done` with the exit code
    at the end. A launcher may create the directory first to hold the run's `log`; a directory that already
    holds a pid or done marker belongs to another run and is never reused."""
    markers = MARKER_ROOT / run_id
    markers.mkdir(parents=True, exist_ok=True)
    for name in ("pid", "done"):
        if (markers / name).exists():
            raise BenchError(f"{markers / name} exists: run id {run_id} was already used")
    (markers / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    return markers


if __name__ == "__main__":
    sys.exit(main())
