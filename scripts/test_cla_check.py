"""Tests for cla_check.py: pure decisions first, then the I/O flows against a stubbed GitHub API."""

from __future__ import annotations

import base64
import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cla_check as cla  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cla.yml"
CONFIG = cla.Config(
    version="3", document="CLA.md", allowlist=("srobroek", "*[bot]"),
    signatures_repo="o/cla-signers", signatures_branch="main", signatures_path="signatures/v1/cla.json",
)
CLA_JSON = ROOT / ".github" / "cla.json"
PHRASE = "I have read the Benchhand CLA version 3 and I hereby sign it."


def commit(user_id: int | None, login: str = "x", message: str = "m", email: str = "a@b.c",
           sha: str = "abc1234") -> dict[str, Any]:
    author = None if user_id is None else {"id": user_id, "login": login}
    return {"sha": sha, "author": author, "commit": {"message": message, "author": {"email": email}}}


def sigs(*entries: tuple[int, str]) -> dict[str, Any]:
    return {
        "signatures": [
            {"id": i, "login": "u", "cla_version": v, "repo": "o/r", "comment_id": 100 + i} for i, v in entries
        ]
    }


class PhraseTests(unittest.TestCase):
    def test_phrase_names_the_version(self) -> None:
        self.assertEqual(cla.signing_phrase("3"), PHRASE)

    def test_exact_match_ignores_surrounding_whitespace_only(self) -> None:
        self.assertTrue(cla.is_signing_comment(f"  {PHRASE}\r\n", CONFIG))

    def test_near_misses_and_wrong_version_do_not_match(self) -> None:
        for body in [
            PHRASE.lower(),
            PHRASE.rstrip("."),
            PHRASE + " thanks",
            "LGTM\n" + PHRASE,
            PHRASE.replace("version 3", "version 2"),
            PHRASE.replace("version 3", "version 30"),
            "> " + PHRASE,
            "",
        ]:
            self.assertFalse(cla.is_signing_comment(body, CONFIG), body)


class AllowlistTests(unittest.TestCase):
    def test_globs(self) -> None:
        patterns = ("srobroek", "*[bot]")
        self.assertTrue(cla.allowlisted("srobroek", patterns))
        self.assertTrue(cla.allowlisted("SRobroek", patterns))
        self.assertTrue(cla.allowlisted("dependabot[bot]", patterns))
        self.assertFalse(cla.allowlisted("srobroek2", patterns))
        self.assertFalse(cla.allowlisted("evilbot", patterns))
        self.assertFalse(cla.allowlisted("bot", patterns))

    def test_brackets_are_literal_not_a_character_class(self) -> None:
        self.assertFalse(cla.allowlisted("b", ("*[bot]",)))


class CoauthorTests(unittest.TestCase):
    def test_parse_trailers_case_insensitively(self) -> None:
        msg = "Fix\n\nBody\n\nCo-authored-by: A B <1+ab@users.noreply.github.com>\nco-authored-by: C <c@x.io>\n"
        self.assertEqual(
            cla.parse_coauthors(msg),
            [("A B", "1+ab@users.noreply.github.com"), ("C", "c@x.io")],
        )

    def test_noreply_with_id_resolves_by_id(self) -> None:
        self.assertEqual(cla.noreply_identity("12345+Octo@users.noreply.github.com"), (12345, "Octo"))

    def test_other_emails_do_not_resolve(self) -> None:
        for email in ["octo@users.noreply.github.com", "a@b.c", "x+y@users.noreply.github.com.evil.io"]:
            self.assertIsNone(cla.noreply_identity(email), email)


class EvaluateTests(unittest.TestCase):
    def run_eval(self, commits: list[dict[str, Any]], opener: tuple[int, str], signed: dict[str, Any]) -> cla.Verdict:
        return cla.evaluate(commits, opener, signed, CONFIG)

    def test_signed_author_passes(self) -> None:
        v = self.run_eval([commit(7, "dev")], (7, "dev"), sigs((7, "3")))
        self.assertTrue(v.ok)

    def test_unsigned_author_fails_and_is_listed_once(self) -> None:
        v = self.run_eval([commit(7, "dev"), commit(7, "dev")], (7, "dev"), sigs())
        self.assertFalse(v.ok)
        self.assertEqual(v.unsigned, [(7, "dev")])

    def test_signature_for_older_version_does_not_count(self) -> None:
        v = self.run_eval([commit(7, "dev")], (7, "dev"), sigs((7, "2")))
        self.assertEqual(v.unsigned, [(7, "dev")])

    def test_signature_matches_by_id_not_login(self) -> None:
        # id 8 renamed to the login of signer 7; only id 7 signed
        v = self.run_eval([commit(8, "dev")], (8, "dev"), sigs((7, "3")))
        self.assertEqual(v.unsigned, [(8, "dev")])
        v = self.run_eval([commit(7, "renamed")], (7, "renamed"), sigs((7, "3")))
        self.assertTrue(v.ok)

    def test_allowlisted_login_needs_no_signature(self) -> None:
        v = self.run_eval([commit(1, "dependabot[bot]"), commit(2, "srobroek")], (2, "srobroek"), sigs())
        self.assertTrue(v.ok)

    def test_null_author_fails_with_link_your_email(self) -> None:
        v = self.run_eval([commit(None, sha="deadbeef1", email="me@home.org")], (7, "dev"), sigs((7, "3")))
        self.assertFalse(v.ok)
        self.assertEqual(v.unlinked, [("deadbeef1", "me@home.org")])
        self.assertIn("link", cla.report(v, CONFIG).lower())
        self.assertIn("me@home.org", cla.report(v, CONFIG))

    def test_opener_who_is_not_an_author_must_also_have_signed(self) -> None:
        v = self.run_eval([commit(7, "dev")], (9, "opener"), sigs((7, "3")))
        self.assertEqual(v.unsigned, [(9, "opener")])
        v = self.run_eval([commit(7, "dev")], (9, "opener"), sigs((7, "3"), (9, "3")))
        self.assertTrue(v.ok)

    def test_noreply_coauthor_is_required_by_id(self) -> None:
        msg = "m\n\nCo-authored-by: Z <55+zed@users.noreply.github.com>"
        v = self.run_eval([commit(7, "dev", message=msg)], (7, "dev"), sigs((7, "3")))
        self.assertEqual(v.unsigned, [(55, "zed")])

    def test_unresolvable_coauthor_warns_but_does_not_fail(self) -> None:
        msg = "m\n\nCo-authored-by: Z <z@example.com>"
        v = self.run_eval([commit(7, "dev", message=msg)], (7, "dev"), sigs((7, "3")))
        self.assertTrue(v.ok)
        self.assertEqual(v.warnings, ["z@example.com"])


class SignatureDocTests(unittest.TestCase):
    record = {"id": 7, "login": "dev", "cla_version": "3", "pr": 4, "comment_id": 99, "signed_at": "t"}

    def test_merge_adds_then_is_idempotent(self) -> None:
        once = cla.merge_signature({"signatures": []}, self.record)
        self.assertEqual(once, {"signatures": [self.record]})
        twice = cla.merge_signature(once, {**self.record, "comment_id": 100})
        self.assertEqual(twice, once)

    def test_merge_adds_new_version_for_same_user(self) -> None:
        old = {"signatures": [{**self.record, "cla_version": "2"}]}
        self.assertEqual(len(cla.merge_signature(old, self.record)["signatures"]), 2)

    def test_merge_tolerates_empty_document(self) -> None:
        self.assertEqual(cla.merge_signature({}, self.record), {"signatures": [self.record]})


class RoutingTests(unittest.TestCase):
    def test_routes(self) -> None:
        pr = {"action": "opened", "pull_request": {}}
        self.assertEqual(cla.route("pull_request", pr), "check")
        self.assertEqual(cla.route("pull_request", {"action": "closed"}), "ignore")
        self.assertEqual(cla.route("merge_group", {"action": "checks_requested"}), "skip")
        self.assertEqual(cla.route("push", {}), "ignore")

    def test_comment_events_on_pull_requests_only(self) -> None:
        for action in ["created", "edited", "deleted"]:
            payload = {"action": action, "issue": {"pull_request": {"url": "u"}}}
            self.assertEqual(cla.route("issue_comment", payload), "comment", action)
        self.assertEqual(cla.route("issue_comment", {"action": "created", "issue": {}}), "ignore")


class CommentActionTests(unittest.TestCase):
    def action(self, action: str, body: str, previous: str | None = None) -> str:
        payload: dict[str, Any] = {"action": action, "comment": {"body": body}}
        if previous is not None:
            payload["changes"] = {"body": {"from": previous}}
        return cla.comment_action(payload, CONFIG)

    def test_created_phrase_signs_and_other_text_is_ignored(self) -> None:
        self.assertEqual(self.action("created", PHRASE), "sign")
        self.assertEqual(self.action("created", "LGTM"), "ignore")

    def test_edit_into_the_phrase_signs(self) -> None:
        self.assertEqual(self.action("edited", PHRASE, previous="draft"), "sign")

    def test_edit_away_from_the_phrase_revokes(self) -> None:
        self.assertEqual(self.action("edited", "never mind", previous=PHRASE), "revoke")

    def test_edit_between_unrelated_texts_is_ignored(self) -> None:
        self.assertEqual(self.action("edited", "b", previous="a"), "ignore")

    def test_deleting_the_phrase_revokes_other_deletes_are_ignored(self) -> None:
        self.assertEqual(self.action("deleted", PHRASE), "revoke")
        self.assertEqual(self.action("deleted", "LGTM"), "ignore")


class RemoveSignatureTests(unittest.TestCase):
    def test_removes_only_the_record_from_that_comment_in_that_repo(self) -> None:
        doc = {"signatures": [
            {"id": 1, "repo": "o/r", "comment_id": 5},
            {"id": 2, "repo": "o/r", "comment_id": 6},
            {"id": 3, "repo": "other/r", "comment_id": 5},
        ]}
        self.assertEqual(
            cla.remove_signatures(doc, "o/r", 5),
            {"signatures": [{"id": 2, "repo": "o/r", "comment_id": 6}, {"id": 3, "repo": "other/r", "comment_id": 5}]},
        )

    def test_unknown_comment_leaves_the_document_equal(self) -> None:
        doc = sigs((7, "3"))
        self.assertEqual(cla.remove_signatures(doc, "o/r", 1), doc)


SIG_URL = "/repos/o/cla-signers/contents/signatures/v1/cla.json"


class FakeGitHub:
    """Stub transport for the REST calls cla_check makes, for the code repo and the signatures repo."""

    def __init__(self, commits: list[dict[str, Any]], doc: dict[str, Any] | None = None,
                 put_failures: list[int] | None = None,
                 runs: list[dict[str, Any]] | None = None) -> None:
        self.commits = commits
        self.doc = doc
        self.put_failures = list(put_failures or [])
        self.calls: list[tuple[str, str, Any]] = []
        self.version = 1
        self.runs = runs if runs is not None else [
            {"id": 11, "status": "completed", "conclusion": "failure"},
            {"id": 10, "status": "completed", "conclusion": "failure"},
        ]

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        if method == "GET" and "/pulls/4/commits" in path:
            page = int(re.search(r"[?&]page=(\d+)", path).group(1))  # type: ignore[union-attr]
            return 200, self.commits[(page - 1) * 100 : page * 100]
        if method == "GET" and path.endswith("/pulls/4"):
            return 200, {"head": {"sha": "headsha"}, "user": {"id": 7, "login": "dev"}}
        if method == "GET" and path == f"{SIG_URL}?ref=main":
            if self.doc is None:
                return 404, {}
            raw = base64.b64encode(json.dumps(self.doc).encode()).decode()
            return 200, {"content": raw, "sha": f"blob{self.version}"}
        if method == "PUT" and path == SIG_URL:
            assert body["branch"] == "main"
            if self.put_failures:
                return self.put_failures.pop(0), {"message": "conflict"}
            self.doc = json.loads(base64.b64decode(body["content"]))
            self.version += 1
            return 200, {}
        if method == "GET" and "/repos/o/r/actions/workflows/cla.yml/runs" in path:
            return 200, {"workflow_runs": self.runs}
        if method == "POST" and "/repos/o/r/actions/runs/" in path:
            return 201, {}
        raise AssertionError(f"unexpected call {method} {path}")


def event(tmp: Path, name: str, payload: dict[str, Any]) -> dict[str, str]:
    path = tmp / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {"GITHUB_EVENT_NAME": name, "GITHUB_EVENT_PATH": str(path), "GITHUB_REPOSITORY": "o/r"}


class FlowTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def go(self, env: dict[str, str], api: FakeGitHub, now: str = "t") -> int:
        return cla.run(env, api, api, CONFIG, now=now)

    def pr_env(self) -> dict[str, str]:
        return event(self.tmp, "pull_request", {
            "action": "opened",
            "pull_request": {"number": 4, "user": {"id": 7, "login": "dev"}},
        })

    def comment_env(self, body: str = PHRASE, user_id: int = 7, action: str = "created",
                    previous: str | None = None) -> dict[str, str]:
        payload: dict[str, Any] = {
            "action": action,
            "issue": {"number": 4, "pull_request": {"url": "u"}, "user": {"id": 7, "login": "dev"}},
            "comment": {"id": 99, "body": body, "user": {"id": user_id, "login": "dev"}},
        }
        if previous is not None:
            payload["changes"] = {"body": {"from": previous}}
        return event(self.tmp, "issue_comment", payload)

    def signed_doc(self) -> dict[str, Any]:
        record = {"id": 7, "login": "dev", "cla_version": "3", "repo": "o/r", "pr": 4,
                  "comment_id": 99, "signed_at": "t"}
        return {"signatures": [record]}

    def test_merge_group_passes_without_any_api_call(self) -> None:
        api = FakeGitHub([])
        env = event(self.tmp, "merge_group", {"action": "checks_requested"})
        self.assertEqual(self.go(env, api), 0)
        self.assertEqual(api.calls, [])

    def test_check_fails_for_unsigned_and_passes_when_signed(self) -> None:
        self.assertEqual(self.go(self.pr_env(), FakeGitHub([commit(7, "dev")], doc=sigs())), 1)
        self.assertEqual(self.go(self.pr_env(), FakeGitHub([commit(7, "dev")], doc=sigs((7, "3")))), 0)

    def test_check_with_no_signature_file_treats_everyone_as_unsigned(self) -> None:
        self.assertEqual(self.go(self.pr_env(), FakeGitHub([commit(7, "dev")], doc=None)), 1)

    def test_check_paginates_commits(self) -> None:
        commits = [commit(7, "dev")] * 100 + [commit(8, "late")]
        self.assertEqual(self.go(self.pr_env(), FakeGitHub(commits, doc=sigs((7, "3")))), 1)

    def test_sign_records_signature_in_the_signatures_repo_and_reruns_failed_run(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs())
        self.assertEqual(self.go(self.comment_env(), api, now="2026-01-01T00:00:00Z"), 0)
        rec = api.doc["signatures"][0]  # type: ignore[index]
        self.assertEqual(
            rec,
            {"id": 7, "login": "dev", "cla_version": "3", "repo": "o/r", "pr": 4, "comment_id": 99,
             "signed_at": "2026-01-01T00:00:00Z"},
        )
        posts = [c[1] for c in api.calls if c[0] == "POST"]
        self.assertEqual(posts, ["/repos/o/r/actions/runs/11/rerun-failed-jobs"])
        self.assertTrue(any("head_sha=headsha" in c[1] for c in api.calls))

    def test_sign_skips_runs_that_did_not_fail(self) -> None:
        runs = [{"id": 12, "status": "completed", "conclusion": "success"}]
        api = FakeGitHub([commit(7, "dev")], doc=sigs(), runs=runs)
        self.assertEqual(self.go(self.comment_env(), api), 0)
        self.assertEqual([c for c in api.calls if c[0] == "POST"], [])

    def test_sign_ignores_non_phrase_comment(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs())
        self.assertEqual(self.go(self.comment_env("LGTM"), api), 0)
        self.assertEqual(api.calls, [])

    def test_sign_refuses_commenter_who_is_neither_author_nor_opener(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs())
        self.assertEqual(self.go(self.comment_env(user_id=666), api), 0)
        self.assertEqual(api.doc, sigs())
        self.assertEqual(api.writes(), [])

    def test_sign_accepts_commit_author_who_is_not_the_opener(self) -> None:
        api = FakeGitHub([commit(5, "co")], doc=sigs())
        self.assertEqual(self.go(self.comment_env(user_id=5), api), 0)
        self.assertEqual(api.doc["signatures"][0]["id"], 5)  # type: ignore[index]

    def test_sign_twice_writes_once(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs((7, "3")))
        self.assertEqual(self.go(self.comment_env(), api), 0)
        self.assertEqual([c for c in api.calls if c[0] == "PUT"], [])

    def test_sign_retries_conflicts_then_succeeds(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs(), put_failures=[409, 422])
        self.assertEqual(self.go(self.comment_env(), api), 0)
        self.assertEqual(len([c for c in api.calls if c[0] == "PUT"]), 3)
        self.assertEqual(len(api.doc["signatures"]), 1)  # type: ignore[index]

    def test_sign_gives_up_after_bounded_retries(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs(), put_failures=[409] * 20)
        self.assertEqual(self.go(self.comment_env(), api), 1)
        self.assertEqual(len([c for c in api.calls if c[0] == "PUT"]), cla.MAX_ATTEMPTS)

    def test_sign_creates_the_signature_file_when_missing(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=None)
        self.assertEqual(self.go(self.comment_env(), api), 0)
        put = next(c for c in api.calls if c[0] == "PUT")
        self.assertNotIn("sha", put[2])
        self.assertEqual(api.doc["signatures"][0]["id"], 7)  # type: ignore[index]

    def test_editing_a_comment_into_the_phrase_signs(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs())
        self.assertEqual(self.go(self.comment_env(action="edited", previous="draft"), api), 0)
        self.assertEqual(api.doc["signatures"][0]["comment_id"], 99)  # type: ignore[index]

    def test_deleting_the_signing_comment_removes_the_record_and_reruns_the_latest_run(self) -> None:
        runs = [{"id": 13, "status": "in_progress", "conclusion": None},
                {"id": 12, "status": "completed", "conclusion": "success"}]
        api = FakeGitHub([commit(7, "dev")], doc=self.signed_doc(), runs=runs)
        self.assertEqual(self.go(self.comment_env(action="deleted"), api), 0)
        self.assertEqual(api.doc, {"signatures": []})
        posts = [c[1] for c in api.calls if c[0] == "POST"]
        self.assertEqual(posts, ["/repos/o/r/actions/runs/12/rerun"])

    def test_editing_the_phrase_away_removes_the_record_and_reruns(self) -> None:
        runs = [{"id": 12, "status": "completed", "conclusion": "success"}]
        api = FakeGitHub([commit(7, "dev")], doc=self.signed_doc(), runs=runs)
        env = self.comment_env("never mind", action="edited", previous=PHRASE)
        self.assertEqual(self.go(env, api), 0)
        self.assertEqual(api.doc, {"signatures": []})
        self.assertEqual([c[1] for c in api.calls if c[0] == "POST"], ["/repos/o/r/actions/runs/12/rerun"])

    def test_deleting_a_comment_with_no_record_changes_nothing(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=sigs())
        self.assertEqual(self.go(self.comment_env(action="deleted"), api), 0)
        self.assertEqual(api.writes(), [])

    def test_deleting_an_unrelated_comment_makes_no_call(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=self.signed_doc())
        self.assertEqual(self.go(self.comment_env("LGTM", action="deleted"), api), 0)
        self.assertEqual(api.calls, [])

    def test_revoke_retries_a_conflict(self) -> None:
        api = FakeGitHub([commit(7, "dev")], doc=self.signed_doc(), put_failures=[409])
        self.assertEqual(self.go(self.comment_env(action="deleted"), api), 0)
        self.assertEqual(api.doc, {"signatures": []})

    def test_match_only_is_true_for_sign_and_revoke_events_only(self) -> None:
        self.assertTrue(cla.matches(self.comment_env(), CONFIG))
        self.assertTrue(cla.matches(self.comment_env(action="deleted"), CONFIG))
        self.assertTrue(cla.matches(self.comment_env("x", action="edited", previous=PHRASE), CONFIG))
        self.assertFalse(cla.matches(self.comment_env("nope"), CONFIG))
        self.assertFalse(cla.matches(self.comment_env("nope", action="deleted"), CONFIG))


class ConfigTests(unittest.TestCase):
    config = cla.parse_config(json.loads(CLA_JSON.read_text(encoding="utf-8")))

    def test_signatures_live_in_the_separate_repository(self) -> None:
        self.assertEqual(
            (self.config.signatures_repo, self.config.signatures_branch, self.config.signatures_path),
            ("benchhand/cla-signers", "main", "signatures/v1/cla.json"),
        )

    def test_allowlist(self) -> None:
        self.assertEqual(
            self.config.allowlist,
            ("dependabot[bot]", "renovate[bot]", "github-actions[bot]", "sjorsr-release-bot[bot]",
             "sjorsr-cla-bot[bot]", "srobroek"),
        )

    def test_the_phrase_names_the_configured_version(self) -> None:
        self.assertTrue(self.config.version)
        self.assertIn(self.config.version, cla.signing_phrase(self.config.version))


class WorkflowTests(unittest.TestCase):
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else ""

    def doc(self) -> dict[str, Any]:
        import yaml
        return yaml.safe_load(self.text)

    def steps(self) -> list[dict[str, Any]]:
        return [s for j in self.doc()["jobs"].values() for s in j["steps"]]

    def test_workflow_exists(self) -> None:
        self.assertTrue(self.text)

    def test_no_pull_request_target(self) -> None:
        self.assertNotIn("pull_request_target", self.text)

    def test_never_checks_out_pr_head(self) -> None:
        for bad in ["pull_request.head", "head.sha", "head.ref", "refs/pull/", "github.head_ref", "merge_commit_sha"]:
            self.assertNotIn(bad, self.text)

    def test_checkouts_do_not_persist_credentials(self) -> None:
        for step in self.steps():
            if str(step.get("uses", "")).startswith("actions/checkout"):
                self.assertIs(step["with"]["persist-credentials"], False)
                self.assertIn("ref", step["with"])

    def test_no_expression_interpolation_in_run_steps(self) -> None:
        for step in self.steps():
            self.assertNotIn("${{", step.get("run", ""))

    def test_actions_are_pinned_by_full_sha(self) -> None:
        for line in re.findall(r"uses:\s*(\S+)", self.text):
            self.assertRegex(line, r"@[0-9a-f]{40}$")

    def test_every_check_job_is_named_cla(self) -> None:
        jobs = self.doc()["jobs"]
        self.assertEqual([jobs[k]["name"] for k in ("cla", "cla-merge-group")], ["CLA", "CLA"])

    def test_check_job_is_read_only_and_has_no_secrets(self) -> None:
        job = self.doc()["jobs"]["cla"]
        self.assertEqual(job["permissions"], {"contents": "read", "pull-requests": "read"})
        self.assertNotIn("secrets.", json.dumps(job))

    def test_merge_group_job_has_no_permissions_or_secrets(self) -> None:
        job = self.doc()["jobs"]["cla-merge-group"]
        self.assertEqual(job["permissions"], {})
        self.assertNotIn("secrets.", json.dumps(job))
        self.assertNotIn("actions/checkout", json.dumps(job))

    def test_comment_trigger_covers_edits_and_deletes(self) -> None:
        on = self.doc()[True]  # PyYAML reads the bare key `on` as True
        self.assertEqual(on["issue_comment"]["types"], ["created", "edited", "deleted"])

    def test_app_tokens_use_secrets_and_the_client_id_input(self) -> None:
        mints = [s for s in self.steps() if str(s.get("uses", "")).startswith("actions/create-github-app-token@")]
        self.assertEqual(len(mints), 2)
        for step in mints:
            self.assertEqual(step["with"]["client-id"], "${{ secrets.CLA_APP_CLIENT_ID }}")
            self.assertEqual(step["with"]["private-key"], "${{ secrets.CLA_APP_PRIVATE_KEY }}")
            self.assertNotIn("app-id", step["with"])

    def test_signatures_token_is_scoped_to_the_signatures_repo(self) -> None:
        step = next(s for s in self.steps() if s.get("id") == "signatures")
        self.assertEqual(step["with"]["owner"], "benchhand")
        self.assertEqual(step["with"]["repositories"], "cla-signers")
        self.assertEqual({k for k in step["with"] if k.startswith("permission-")}, {"permission-contents"})
        self.assertEqual(step["with"]["permission-contents"], "write")

    def test_rerun_token_cannot_write_contents(self) -> None:
        step = next(s for s in self.steps() if s.get("id") == "rerun")
        self.assertEqual(step["with"]["permission-actions"], "write")
        self.assertNotIn("permission-contents", step["with"])


if __name__ == "__main__":
    unittest.main()
