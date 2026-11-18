"""Guards the release-please setup: config, manifest, version files and the workflow."""

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ROOT / ".release-please-manifest.json"
WORKFLOW = ROOT / ".github" / "workflows" / "release-please.yml"
VERSION_FILES = [ROOT / ".claude-plugin" / "plugin.json", ROOT / ".claude-plugin" / "marketplace.json"]
SHA_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(document: object, jsonpath: str) -> object:
    """Resolve the simple `$.key[0].key` subset of JSONPath that extra-files use."""
    assert jsonpath.startswith("$"), jsonpath
    node = document
    for key, index in re.findall(r"\.([\w-]+)|\[(\d+)\]", jsonpath):
        node = node[int(index)] if index else node[key]
    return node


def package() -> dict:
    return load(CONFIG)["packages"]["."]


def manifest_version() -> str:
    return load(MANIFEST)["."]


class ManifestTests(unittest.TestCase):
    def test_manifest_is_the_latest_tagged_version(self) -> None:
        self.assertEqual(manifest_version(), "0.1.1")

    def test_version_files_that_carry_a_version_match_the_manifest(self) -> None:
        # plugin.json and marketplace.json hold no version today; if one is added it must agree.
        for path in VERSION_FILES:
            for found in re.findall(r'"version"\s*:\s*"([^"]+)"', path.read_text(encoding="utf-8")):
                self.assertEqual(found, manifest_version(), path.name)


class ChangelogTests(unittest.TestCase):
    # release-please inserts new entries above the first match of this pattern.
    ENTRY_HEADING = re.compile(r"\n###? v?[0-9[]")

    def test_first_release_heading_is_the_manifest_version(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        match = self.ENTRY_HEADING.search(text)
        self.assertIsNotNone(match)
        heading = text[match.start() + 1 :].splitlines()[0]
        self.assertRegex(heading, rf"^## v?{re.escape(manifest_version())}( |$)")


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
