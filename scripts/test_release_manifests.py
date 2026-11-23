# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every marketplace and package manifest stays at the release version.

release-please (type `simple`) bumps only CHANGELOG.md and `.release-please-manifest.json`. The
`extra-files` entries in `release-please-config.json` make a release PR bump the other manifests:
the Claude plugin and marketplace files (which Codex also reads, as its legacy-compatible
marketplace) and the root `package.json` that pi installs. These tests fail when a manifest is left
out of `extra-files`, when a version drifts from the release manifest, or when the pinned
release-please does not bump all three on a release. Claude reads `plugin.json` before
`marketplace.json`, so a drifted `plugin.json` would hide a marketplace bump.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "release-please-config.json"
TOOLS = Path(__file__).with_name("release_tools")

# The release-please that the pinned action (googleapis/release-please-action v5.0.0, see
# .github/workflows/release-please.yml) bundles. Its log line reads "release-please version 17.6.0".
RELEASE_PLEASE = "17.6.0"
MARKETPLACE_ENTRY = "benchhand"

# The manifests a release must bump, each with the JSONPath of its version. The list is the policy;
# the config under test has to match it exactly. The marketplace entry is picked by name, so a
# second plugin listed before it cannot take its bump.
VERSIONED = {
    ".claude-plugin/plugin.json": "$.version",
    ".claude-plugin/marketplace.json": f"$.plugins[?(@.name=='{MARKETPLACE_ENTRY}')].version",
    "package.json": "$.version",
}


def as_object(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return value


def as_list(value: object) -> list[object]:
    assert isinstance(value, list), value
    return value


def as_str(value: object) -> str:
    assert isinstance(value, str), value
    return value


def parse(text: str) -> dict[str, object]:
    return as_object(json.loads(text))


def load(relative: str) -> dict[str, object]:
    return parse((ROOT / relative).read_text(encoding="utf-8"))


def marketplace_entry(marketplace: dict[str, object]) -> dict[str, object]:
    """The one plugin entry named `MARKETPLACE_ENTRY`, wherever it sits in the list."""
    entries = [
        entry
        for entry in map(as_object, as_list(marketplace["plugins"]))
        if entry["name"] == MARKETPLACE_ENTRY
    ]
    assert len(entries) == 1, entries
    return entries[0]


def versions(files: dict[str, dict[str, object]]) -> dict[str, str]:
    """The version each versioned manifest declares, by the same rule as `VERSIONED`."""
    return {
        ".claude-plugin/plugin.json": as_str(files[".claude-plugin/plugin.json"]["version"]),
        ".claude-plugin/marketplace.json": as_str(
            marketplace_entry(files[".claude-plugin/marketplace.json"])["version"]
        ),
        "package.json": as_str(files["package.json"]["version"]),
    }


def extra_files() -> dict[str, str]:
    package = as_object(as_object(load("release-please-config.json")["packages"])["."])
    entries = [as_object(entry) for entry in as_list(package.get("extra-files", []))]
    assert all(entry.get("type") == "json" for entry in entries), entries
    return {as_str(entry["path"]): as_str(entry["jsonpath"]) for entry in entries}


class ReleaseVersionTests(unittest.TestCase):
    def test_config_bumps_exactly_the_versioned_manifests(self) -> None:
        self.assertEqual(extra_files(), VERSIONED)

    def test_every_manifest_version_equals_the_release_manifest(self) -> None:
        release = as_str(load(".release-please-manifest.json")["."])
        current = versions({relative: load(relative) for relative in VERSIONED})
        self.assertEqual(current, dict.fromkeys(VERSIONED, release))


def with_version(files: dict[str, dict[str, object]], version: str) -> dict[str, dict[str, object]]:
    """A copy of `files` in which only the three versions the release must change are replaced."""
    changed = copy.deepcopy(files)
    changed[".claude-plugin/plugin.json"]["version"] = version
    marketplace_entry(changed[".claude-plugin/marketplace.json"])["version"] = version
    changed["package.json"]["version"] = version
    return changed


class ReleaseRunTests(unittest.TestCase):
    """Runs the pinned release-please on a repository's config and checks what it would write.

    The tools install from the committed lockfile with `npm ci`, so the run uses exactly the
    release-please that the lockfile resolves, with every dependency checked against its integrity
    hash. CI provisions Node (`.github/workflows/ci.yml`); there a missing tool fails the test, as a
    skip would hide a release that no longer bumps a manifest.
    """

    install: Path

    @classmethod
    def setUpClass(cls) -> None:
        node, npm = shutil.which("node"), shutil.which("npm")
        if node is None or npm is None:
            if os.environ.get("CI"):
                raise AssertionError("node and npm are required in CI to run the pinned release-please")
            raise unittest.SkipTest("node and npm are needed to run the pinned release-please")
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        cls.install = Path(scratch.name)
        for name in ("package.json", "package-lock.json"):
            shutil.copy(TOOLS / name, cls.install / name)
        subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=cls.install, check=True, capture_output=True, text=True, timeout=300,
        )

    def release(self, root: Path, version: str) -> dict[str, dict[str, object]]:
        done = subprocess.run(
            ["node", str(TOOLS / "bump.cjs"), str(self.install), str(root), version],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return {path: parse(as_str(content)) for path, content in parse(done.stdout).items()}

    def test_the_lockfile_resolves_the_pinned_release_please(self) -> None:
        installed = parse((self.install / "node_modules/release-please/package.json").read_text(encoding="utf-8"))
        self.assertEqual(installed["version"], RELEASE_PLEASE)

    def test_a_release_rewrites_exactly_the_three_versions(self) -> None:
        before = {relative: load(relative) for relative in VERSIONED}
        after = self.release(ROOT, "9.9.9")
        self.assertEqual(after, with_version(before, "9.9.9"))

    def test_a_release_bumps_benchhand_when_another_plugin_is_listed_first(self) -> None:
        files = {relative: load(relative) for relative in VERSIONED}
        marketplace = files[".claude-plugin/marketplace.json"]
        other = {"name": "other", "version": "1.0.0", "source": "./other"}
        marketplace["plugins"] = [other, *as_list(marketplace["plugins"])]
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            shutil.copy(CONFIG, root / CONFIG.name)
            for relative, document in files.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text(json.dumps(document, indent=2), encoding="utf-8")
            after = self.release(root, "9.9.9")
        self.assertEqual(after, with_version(files, "9.9.9"))
        self.assertEqual(as_list(after[".claude-plugin/marketplace.json"]["plugins"])[0], other)


class ManifestPathTests(unittest.TestCase):
    def test_claude_plugin_agents_exist(self) -> None:
        agents = as_list(load(".claude-plugin/plugin.json")["agents"])
        self.assertTrue(agents)
        for relative in map(as_str, agents):
            with self.subTest(agent=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_marketplace_plugin_source_is_the_repository_root(self) -> None:
        marketplace = load(".claude-plugin/marketplace.json")
        plugin = marketplace_entry(marketplace)
        self.assertEqual(plugin["name"], load(".claude-plugin/plugin.json")["name"])
        self.assertEqual((ROOT / as_str(plugin["source"])).resolve(), ROOT)

    def test_pi_package_declares_the_skills_directory_and_the_gallery_keyword(self) -> None:
        package = load("package.json")
        self.assertEqual(package["name"], "benchhand")
        self.assertIn("pi-package", as_list(package["keywords"]))
        self.assertEqual(as_object(package["pi"])["skills"], ["./skills"])
        self.assertTrue((ROOT / "skills/cad-flow/SKILL.md").is_file())

    def test_every_path_the_pi_package_declares_exists(self) -> None:
        for kind, paths in as_object(load("package.json")["pi"]).items():
            for relative in map(as_str, as_list(paths)):
                with self.subTest(kind=kind, path=relative):
                    self.assertTrue((ROOT / relative).exists())


if __name__ == "__main__":
    unittest.main()
