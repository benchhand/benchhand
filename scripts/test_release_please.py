# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guards the release-please setup: config, manifest, version files and the workflow."""

import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "release-please-config.json"
WORKFLOW = ROOT / ".github" / "workflows" / "release-please.yml"
# Only this marketplace entry follows the release; another plugin listed there keeps its own version.
MARKETPLACE_ENTRY = "benchhand"
SHA_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(document: object, jsonpath: str) -> object:
    """Resolve the `$.key`, `[0]` and `[?(@.key=='value')]` JSONPath subset that extra-files use.

    A filter must match exactly one element, as release-please writes one version per entry.
    """
    assert jsonpath.startswith("$"), jsonpath
    node = document
    for key, index, field, value in re.findall(r"\.([\w-]+)|\[(\d+)\]|\[\?\(@\.([\w-]+)=='([^']*)'\)\]", jsonpath):
        if field:
            matches = [item for item in node if item[field] == value]
            assert len(matches) == 1, (jsonpath, matches)
            node = matches[0]
        else:
            node = node[int(index)] if index else node[key]
    return node


def package() -> dict:
    return load(CONFIG)["packages"]["."]


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
# release-please inserts new entries above the first match of this pattern.
ENTRY_HEADING = re.compile(r"\n###? v?[0-9[]")
# `## 0.2.0 (date)`, `## v0.2.0`, or release-please's `## [0.2.0](url) (date)`.
HEADING_VERSION = re.compile(r"^###? v?\[?([^\]\s]+)")


def release_state_problems(root: Path) -> list[str]:
    """Return how the version records under `root` disagree; empty when they agree.

    The manifest is the source of truth, so the check holds across release PRs, which bump
    the manifest, CHANGELOG.md and version.txt together. `## Unreleased` is optional.
    """
    problems: list[str] = []
    version = json.loads((root / ".release-please-manifest.json").read_text(encoding="utf-8"))["."]
    if not SEMVER.match(version):
        problems.append(f"manifest version {version!r} is not semver")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = ENTRY_HEADING.search(changelog)
    if match is None:
        problems.append("CHANGELOG.md has no release heading")
    else:
        heading = changelog[match.start() + 1 :].splitlines()[0]
        found = HEADING_VERSION.match(heading)
        if found is None or found.group(1) != version:
            problems.append(f"first CHANGELOG release heading {heading!r} is not {version}")

    plugin = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    entries = [entry for entry in marketplace["plugins"] if entry["name"] == MARKETPLACE_ENTRY]
    if len(entries) != 1:
        problems.append(f"marketplace.json lists {len(entries)} plugins named {MARKETPLACE_ENTRY}, not one")
    recorded = {".claude-plugin/plugin.json": plugin["version"]}
    if entries:
        recorded[f".claude-plugin/marketplace.json ({MARKETPLACE_ENTRY})"] = entries[0]["version"]
    for relative, found_version in recorded.items():
        if found_version != version:
            problems.append(f"{relative} version {found_version} is not {version}")

    version_txt = root / "version.txt"
    if version_txt.exists() and version_txt.read_text(encoding="utf-8").strip() != version:
        problems.append(f"version.txt is not {version}")
    return problems


def write_release_state(
    root: Path,
    version: str,
    *,
    unreleased: bool,
    version_txt: str | None = None,
    other_plugin: dict[str, str] | None = None,
) -> None:
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}', encoding="utf-8")
    plugins = [*([other_plugin] if other_plugin else []), {"name": "benchhand", "version": version}]
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": plugins}), encoding="utf-8")
    (root / ".release-please-manifest.json").write_text(json.dumps({".": version}), encoding="utf-8")
    sections = ["# Changelog\n", "## Unreleased\n\n- pending\n"] if unreleased else ["# Changelog\n"]
    sections += [
        f"## [{version}](https://example.test/compare) (2027-01-01)\n\n### Features\n\n- new\n",
        "## 0.1.1 (2026-11-02)\n\n- fix\n",
    ]
    (root / "CHANGELOG.md").write_text("\n".join(sections), encoding="utf-8")
    if version_txt is not None:
        (root / "version.txt").write_text(version_txt + "\n", encoding="utf-8")


class ReleaseStateTests(unittest.TestCase):
    def test_repository_release_state_is_consistent(self) -> None:
        self.assertEqual(release_state_problems(ROOT), [])

    def test_bumped_release_pr_state_passes_with_or_without_unreleased(self) -> None:
        for unreleased in (True, False):
            with self.subTest(unreleased=unreleased), tempfile.TemporaryDirectory() as tmp:
                write_release_state(Path(tmp), "0.2.0", unreleased=unreleased, version_txt="0.2.0")
                self.assertEqual(release_state_problems(Path(tmp)), [])

    def test_every_disagreeing_record_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_release_state(root, "0.2.0", unreleased=False, version_txt="0.1.1")
            (root / ".claude-plugin" / "plugin.json").write_text('{"version": "0.1.1"}', encoding="utf-8")
            problems = release_state_problems(root)
        self.assertEqual(len(problems), 2, problems)  # plugin.json and version.txt

    def test_another_plugin_keeps_its_own_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_release_state(
                Path(tmp), "0.2.0", unreleased=False, other_plugin={"name": "other", "version": "1.0.0"}
            )
            self.assertEqual(release_state_problems(Path(tmp)), [])

    def test_a_stale_benchhand_marketplace_entry_is_reported_behind_another_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_release_state(root, "0.2.0", unreleased=False, other_plugin={"name": "other", "version": "0.2.0"})
            stale = {"plugins": [{"name": "other", "version": "0.2.0"}, {"name": "benchhand", "version": "0.1.1"}]}
            (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(stale), encoding="utf-8")
            problems = release_state_problems(root)
        self.assertEqual(len(problems), 1, problems)

    def test_invalid_manifest_version_and_stale_changelog_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_release_state(root, "0.2.0", unreleased=True)
            (root / ".release-please-manifest.json").write_text('{".": "0.3"}', encoding="utf-8")
            problems = release_state_problems(root)
        self.assertEqual(len(problems), 4, problems)  # not semver, changelog heading, plugin.json, marketplace


class ConfigTests(unittest.TestCase):
    def test_tag_and_release_type_settings(self) -> None:
        config = load(CONFIG)
        self.assertEqual(config["release-type"], "simple")
        self.assertIs(config["include-component-in-tag"], False)
        self.assertIs(config["include-v-in-tag"], True)
        self.assertEqual(package()["changelog-path"], "CHANGELOG.md")

    def test_bootstrap_sha_is_a_full_sha(self) -> None:
        self.assertRegex(load(CONFIG)["bootstrap-sha"], r"^[0-9a-f]{40}$")

    def test_every_extra_file_path_and_jsonpath_exists(self) -> None:
        for entry in package().get("extra-files", []):
            target = ROOT / (entry if isinstance(entry, str) else entry["path"])
            self.assertTrue(target.is_file(), target)
            if isinstance(entry, dict) and entry.get("type") == "json":
                resolve(load(target), entry["jsonpath"])


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)
        cls.job = cls.workflow["jobs"]["release-please"]

    def test_every_action_is_pinned_by_full_sha(self) -> None:
        uses = [step["uses"] for step in self.job["steps"] if "uses" in step]
        self.assertEqual(len(uses), 2)
        for ref in uses:
            self.assertRegex(ref, SHA_REF)

    def test_permissions_are_minimal(self) -> None:
        self.assertEqual(self.workflow["permissions"], {})
        self.assertEqual(self.job["permissions"], {"contents": "read"})

    def test_runs_on_main_pushes_and_manual_dispatch(self) -> None:
        triggers = self.workflow[True]  # PyYAML reads the bare key `on` as True
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", triggers)

    def test_job_is_guarded_by_configuration_and_repository(self) -> None:
        condition = self.job["if"]
        self.assertIn("vars.RELEASE_APP_CLIENT_ID != ''", condition)
        self.assertIn("github.repository == 'benchhand/benchhand'", condition)

    def test_app_token_and_release_please_wiring(self) -> None:
        token, release = self.job["steps"]
        self.assertEqual(token["with"]["client-id"], "${{ vars.RELEASE_APP_CLIENT_ID }}")
        self.assertEqual(token["with"]["private-key"], "${{ secrets.RELEASE_APP_PRIVATE_KEY }}")
        self.assertEqual(token["with"]["permission-contents"], "write")
        self.assertEqual(token["with"]["permission-pull-requests"], "write")
        self.assertEqual(release["with"]["config-file"], "release-please-config.json")
        self.assertEqual(release["with"]["manifest-file"], ".release-please-manifest.json")
        self.assertEqual(release["with"]["token"], f"${{{{ steps.{token['id']}.outputs.token }}}}")


if __name__ == "__main__":
    unittest.main()
