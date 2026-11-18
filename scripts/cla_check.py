"""In-repo CLA check: verify that every contributor on a pull request signed the current CLA.

Two flows, chosen by the workflow event (``route``):

* ``check`` (pull_request): list the PR's commits and fail unless every author, and the PR opener,
  is allowlisted or has a signature for the current CLA version in ``signatures/cla.json`` on the
  ``cla-signatures`` branch.
* ``sign`` (issue_comment on a PR): when the comment is exactly the signing phrase and the commenter
  is a commit author or the opener, record the signature on that branch, then re-run the latest
  failed CLA run for the PR head.

Identity is the numeric GitHub user id; logins change. Only the allowlist matches by login.
Nothing here executes PR content, and the comment body is read from the event payload, never from
a shell expression. Stdlib only. The pure functions come first; HTTP and file access sit at the end.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW_FILE = "cla.yml"
MAX_ATTEMPTS = 5
MAX_OPEN_PR_PAGES = 5
PAGE_SIZE = 100
CONFIG_PATH = Path(__file__).resolve().parent.parent / ".github" / "cla.json"
COAUTHOR = re.compile(r"^co-authored-by:\s*(.*?)\s*<([^<>]*)>\s*$", re.IGNORECASE | re.MULTILINE)
NOREPLY = re.compile(r"^(\d+)\+([A-Za-z0-9-]+(?:\[bot\])?)@users\.noreply\.github\.com$")

Transport = Callable[[str, str, Any], "tuple[int, Any]"]


@dataclass(frozen=True)
class Config:
    version: str
    document: str
    allowlist: tuple[str, ...]
    signatures_repo: str = "benchhand/cla-signers"
    signatures_branch: str = "main"
    signatures_path: str = "signatures/v1/cla.json"


@dataclass
class Verdict:
    unsigned: list[tuple[int, str]] = field(default_factory=list)
    unlinked: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsigned and not self.unlinked


def parse_config(raw: dict[str, Any]) -> Config:
    return Config(
        version=str(raw["version"]),
        document=str(raw["document"]),
        allowlist=tuple(raw.get("allowlist", [])),
        signatures_repo=str(raw["signatures_repo"]),
        signatures_branch=str(raw["signatures_branch"]),
        signatures_path=str(raw["signatures_path"]),
    )


def signing_phrase(version: str) -> str:
    return f"I have read the Benchhand CLA version {version} and I hereby sign it."


def is_signing_comment(body: str, config: Config) -> bool:
    return body.strip() == signing_phrase(config.version)


def allowlisted(login: str, patterns: tuple[str, ...]) -> bool:
    """Case-insensitive login match where ``*`` is the only wildcard (``[bot]`` is literal)."""
    for pattern in patterns:
        regex = ".*".join(re.escape(part) for part in pattern.lower().split("*"))
        if re.fullmatch(regex, login.lower()):
            return True
    return False


def parse_coauthors(message: str) -> list[tuple[str, str]]:
    return [(name, email) for name, email in COAUTHOR.findall(message)]


def noreply_identity(email: str) -> tuple[int, str] | None:
    match = NOREPLY.match(email)
    return (int(match.group(1)), match.group(2)) if match else None


def signed_ids(doc: dict[str, Any], version: str) -> set[int]:
    return {s["id"] for s in doc.get("signatures", []) if s.get("cla_version") == version}


def evaluate(
    commits: list[dict[str, Any]], opener: tuple[int, str], doc: dict[str, Any], config: Config
) -> Verdict:
    """Decide who still has to sign. Unlinked authors fail; unverifiable co-authors only warn."""
    verdict = Verdict()
    required: dict[int, str] = {opener[0]: opener[1]}
    for c in commits:
        author = c.get("author")
        message = c["commit"].get("message", "")
        if author is None:
            email = c["commit"].get("author", {}).get("email", "")
            verdict.unlinked.append((c["sha"], email))
        else:
            required.setdefault(author["id"], author["login"])
        for _name, email in parse_coauthors(message):
            identity = noreply_identity(email)
            if identity is None:
                if email not in verdict.warnings:
                    verdict.warnings.append(email)
            else:
                required.setdefault(*identity)
    have = signed_ids(doc, config.version)
    verdict.unsigned = [
        (uid, login)
        for uid, login in required.items()
        if uid not in have and not allowlisted(login, config.allowlist)
    ]
    return verdict


def report(verdict: Verdict, config: Config) -> str:
    lines: list[str] = []
    if verdict.unsigned:
        who = ", ".join(f"@{login}" for _id, login in verdict.unsigned)
        lines += [
            f"CLA not signed by: {who}.",
            f"Read {config.document}, then comment exactly this on the pull request:",
            f"    {signing_phrase(config.version)}",
        ]
    for sha, email in verdict.unlinked:
        lines.append(
            f"Commit {sha[:7]} has author email {email or '(none)'}, which is not linked to a "
            "GitHub account. Link your email in GitHub settings or rewrite the commit author."
        )
    for email in verdict.warnings:
        lines.append(f"Warning: Co-authored-by {email} cannot be verified and was not checked.")
    return "\n".join(lines) or "CLA satisfied."


def merge_signature(doc: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Add the record unless that user already signed that version; the first record wins."""
    existing = doc.get("signatures", [])
    if any(s.get("id") == record["id"] and s.get("cla_version") == record["cla_version"] for s in existing):
        return {**doc, "signatures": list(existing)}
    return {**doc, "signatures": [*existing, record]}


def route(event_name: str, payload: dict[str, Any]) -> str:
    action = payload.get("action")
    if event_name == "merge_group":
        return "skip"
    if event_name == "pull_request" and action in {"opened", "synchronize", "reopened"}:
        return "check"
    if (
        event_name == "issue_comment"
        and action in {"created", "edited", "deleted"}
        and payload.get("issue", {}).get("pull_request")
    ):
        return "comment"
    return "ignore"


def comment_action(payload: dict[str, Any], config: Config) -> str:
    """What a PR comment event means: ``sign``, ``revoke`` (a signing comment was edited away or
    deleted) or ``ignore``. The previous body of an edit comes from ``changes.body.from``."""
    action = payload.get("action")
    body = payload["comment"]["body"]
    if action in {"created", "edited"} and is_signing_comment(body, config):
        return "sign"
    if action == "edited":
        previous = payload.get("changes", {}).get("body", {}).get("from", "")
        return "revoke" if is_signing_comment(previous, config) else "ignore"
    if action == "deleted" and is_signing_comment(body, config):
        return "revoke"
    return "ignore"


def drop_comments(doc: dict[str, Any], repo: str, comment_ids: set[int]) -> dict[str, Any]:
    """Drop the records written for those comments of that repository."""
    kept = [
        s for s in doc.get("signatures", [])
        if not (s.get("repo") == repo and s.get("comment_id") in comment_ids)
    ]
    return {**doc, "signatures": kept}


def remove_signatures(doc: dict[str, Any], repo: str, comment_id: int) -> dict[str, Any]:
    """Drop the records from one comment; a record from another comment stays."""
    return drop_comments(doc, repo, {comment_id})


# ---- I/O ----------------------------------------------------------------------------------------


def github_transport(token: str, api_url: str = "https://api.github.com") -> Transport:
    def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(api_url + path, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode()
                return response.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as err:
            text = err.read().decode()
            try:
                return err.code, json.loads(text) if text else {}
            except ValueError:
                return err.code, {"message": text}

    return call


def expect(status: int, ok: tuple[int, ...], what: str) -> None:
    if status not in ok:
        raise RuntimeError(f"{what} failed with HTTP {status}")


def list_commits(api: Transport, repo: str, number: int) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    page = 1
    while True:
        status, data = api("GET", f"/repos/{repo}/pulls/{number}/commits?per_page={PAGE_SIZE}&page={page}", None)
        expect(status, (200,), "listing pull request commits")
        commits += data
        if len(data) < PAGE_SIZE:
            return commits
        page += 1


def read_signatures(api: Transport, config: Config) -> tuple[dict[str, Any], str | None]:
    path = f"/repos/{config.signatures_repo}/contents/{config.signatures_path}?ref={config.signatures_branch}"
    status, data = api("GET", path, None)
    if status == 404:
        return {"signatures": []}, None
    expect(status, (200,), "reading signatures")
    return json.loads(base64.b64decode(data["content"])), data["sha"]


def update_signatures(
    api: Transport, config: Config, change: Callable[[dict[str, Any]], dict[str, Any]], message: str
) -> str:
    """Apply ``change`` to the signature file with the blob sha, retrying conflicts a bounded number
    of times. Returns ``unchanged``, ``written`` or ``conflict``."""
    for _ in range(MAX_ATTEMPTS):
        doc, sha = read_signatures(api, config)
        updated = change(doc)
        if updated == doc:
            return "unchanged"
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode((json.dumps(updated, indent=2) + "\n").encode()).decode(),
            "branch": config.signatures_branch,
        }
        if sha is not None:
            body["sha"] = sha
        status, _ = api("PUT", f"/repos/{config.signatures_repo}/contents/{config.signatures_path}", body)
        if status in (200, 201):
            return "written"
        if status not in (409, 422):
            raise RuntimeError(f"writing signatures failed with HTTP {status}")
    return "conflict"


def rerun_check(api: Transport, repo: str, head_sha: str, *, failed_only: bool) -> None:
    """Re-run the newest CLA run for the head commit. Fork runs list no pull requests, so match by
    head sha and workflow file. A grant re-runs a failed run; a revoke re-runs a finished run."""
    query = f"head_sha={head_sha}&event=pull_request&per_page=20"
    status, data = api("GET", f"/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?{query}", None)
    expect(status, (200,), "listing CLA runs")
    runs = data.get("workflow_runs", [])
    if failed_only:
        wanted = [r for r in runs if r.get("conclusion") == "failure"]
    else:
        wanted = [r for r in runs if r.get("status", "completed") == "completed"]
    if wanted:
        suffix = "rerun-failed-jobs" if failed_only else "rerun"
        status, _ = api("POST", f"/repos/{repo}/actions/runs/{wanted[0]['id']}/{suffix}", None)
        expect(status, (201,), "re-running the CLA check")


def check(api: Transport, sig_api: Transport, repo: str, pr: dict[str, Any], config: Config) -> int:
    doc, _ = read_signatures(sig_api, config)
    opener = (pr["user"]["id"], pr["user"]["login"])
    verdict = evaluate(list_commits(api, repo, pr["number"]), opener, doc, config)
    print(report(verdict, config))
    return 0 if verdict.ok else 1


def stale_comments(
    api: Transport, repo: str, doc: dict[str, Any], user_id: int, seen: dict[int, bool]
) -> set[int]:
    """Comment ids of the user's records whose source comment is gone (404) or no longer exactly the
    phrase of the record's version. A lookup error keeps the record: only proof drops it."""
    stale: set[int] = set()
    for rec in doc.get("signatures", []):
        cid = rec.get("comment_id")
        if rec.get("id") != user_id or rec.get("repo") != repo or cid is None:
            continue
        if cid not in seen:
            status, data = api("GET", f"/repos/{repo}/issues/comments/{cid}", None)
            if status == 404:
                seen[cid] = False
            elif status == 200:
                seen[cid] = str(data.get("body", "")).strip() == signing_phrase(str(rec.get("cla_version")))
            else:
                seen[cid] = True
        if not seen[cid]:
            stale.add(cid)
    return stale


def reconcile(
    api: Transport, repo: str, doc: dict[str, Any], user_id: int, seen: dict[int, bool]
) -> dict[str, Any]:
    return drop_comments(doc, repo, stale_comments(api, repo, doc, user_id, seen))


def open_pulls(api: Transport, repo: str) -> list[dict[str, Any]]:
    pulls: list[dict[str, Any]] = []
    for page in range(1, MAX_OPEN_PR_PAGES + 1):
        status, data = api("GET", f"/repos/{repo}/pulls?state=open&per_page={PAGE_SIZE}&page={page}", None)
        expect(status, (200,), "listing open pull requests")
        pulls += data
        if len(data) < PAGE_SIZE:
            break
    return pulls


def pulls_of_user(api: Transport, repo: str, user_id: int, own: dict[str, Any]) -> list[dict[str, Any]]:
    """Open pull requests that have the user as opener or commit author, plus the comment's own."""
    found = {own["number"]: own}
    for pull in open_pulls(api, repo):
        if pull["number"] in found:
            continue
        if pull["user"]["id"] == user_id or any(
            c.get("author") and c["author"]["id"] == user_id for c in list_commits(api, repo, pull["number"])
        ):
            found[pull["number"]] = pull
    return list(found.values())


def sign(api: Transport, sig_api: Transport, repo: str, payload: dict[str, Any], config: Config, now: str) -> int:
    comment, number = payload["comment"], payload["issue"]["number"]
    user = comment["user"]
    status, pull = api("GET", f"/repos/{repo}/pulls/{number}", None)
    expect(status, (200,), "reading the pull request")
    commits = list_commits(api, repo, number)
    eligible = {pull["user"]["id"]} | {c["author"]["id"] for c in commits if c.get("author")}
    if user["id"] not in eligible:
        print(f"@{user['login']} is neither the opener nor a commit author; signature not recorded.")
        return 0
    record = {
        "id": user["id"], "login": user["login"], "cla_version": config.version, "repo": repo,
        "pr": number, "comment_id": comment["id"], "signed_at": now,
    }
    seen: dict[int, bool] = {}
    outcome = update_signatures(
        sig_api, config, lambda doc: reconcile(api, repo, merge_signature(doc, record), user["id"], seen),
        f"cla: {user['login']} signed version {config.version} on {repo}#{number}",
    )
    if outcome == "conflict":
        print("Could not record the signature after repeated conflicts; comment again to retry.")
        return 1
    print(f"Recorded @{user['login']} for CLA version {config.version}.")
    rerun_check(api, repo, pull["head"]["sha"], failed_only=True)
    return 0


def revoke(api: Transport, sig_api: Transport, repo: str, payload: dict[str, Any], config: Config) -> int:
    comment_id, number = payload["comment"]["id"], payload["issue"]["number"]
    user_id = payload["comment"]["user"]["id"]
    seen: dict[int, bool] = {}
    outcome = update_signatures(
        sig_api, config,
        lambda doc: reconcile(api, repo, remove_signatures(doc, repo, comment_id), user_id, seen),
        f"cla: remove the signature from edited or deleted comment {comment_id} on {repo}#{number}",
    )
    if outcome == "conflict":
        print("Could not remove the signature after repeated conflicts; re-run this job.")
        return 1
    if outcome == "unchanged":
        return 0
    print(f"Removed the signature recorded from comment {comment_id}.")
    status, own = api("GET", f"/repos/{repo}/pulls/{number}", None)
    expect(status, (200,), "reading the pull request")
    own = {**own, "number": number}
    failed = 0
    # Signatures are global: every open pull request of this user loses the pass, not just this one.
    for pull in pulls_of_user(api, repo, user_id, own):
        try:
            rerun_check(api, repo, pull["head"]["sha"], failed_only=False)
        except RuntimeError as err:
            print(f"Could not re-run the CLA check for #{pull['number']}: {err}")
            failed = 1
    return failed


def load_payload(env: dict[str, str]) -> dict[str, Any]:
    return json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))


def matches(env: dict[str, str], config: Config) -> bool:
    """True when the event needs the app token: a signing comment, or one edited away or deleted."""
    payload = load_payload(env)
    return route(env["GITHUB_EVENT_NAME"], payload) == "comment" and comment_action(payload, config) != "ignore"


def run(env: dict[str, str], api: Transport, sig_api: Transport, config: Config, now: str | None = None) -> int:
    payload = load_payload(env)
    repo = env["GITHUB_REPOSITORY"]
    match route(env["GITHUB_EVENT_NAME"], payload):
        case "check":
            return check(api, sig_api, repo, payload["pull_request"], config)
        case "comment":
            match comment_action(payload, config):
                case "sign":
                    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    return sign(api, sig_api, repo, payload, config, stamp)
                case "revoke":
                    return revoke(api, sig_api, repo, payload, config)
                case _:
                    return 0
        case "skip":
            print("Merge queue: the pull request already passed the CLA check.")
            return 0
        case _:
            return 0


def main(argv: list[str]) -> int:
    env = dict(os.environ)
    config = parse_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if argv == ["--match-only"]:
        out = env.get("GITHUB_OUTPUT")
        line = f"matches={'true' if matches(env, config) else 'false'}\n"
        if out:
            with open(out, "a", encoding="utf-8") as handle:
                handle.write(line)
        else:
            print(line, end="")
        return 0
    url = env.get("GITHUB_API_URL", "https://api.github.com")
    # SIGNATURES_TOKEN writes the signatures repo; GITHUB_TOKEN reads and re-runs on this one. The
    # check job has no SIGNATURES_TOKEN and reads the public signatures repo with its own token.
    api = github_transport(env.get("GITHUB_TOKEN", ""), url)
    sig_api = github_transport(env.get("SIGNATURES_TOKEN") or env.get("GITHUB_TOKEN", ""), url)
    return run(env, api, sig_api, config)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
