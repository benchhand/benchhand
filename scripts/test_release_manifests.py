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

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUMP = Path(__file__).with_name("release_please_bump.cjs")

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


def versions(files: dict[str, dict[str, object]]) -> dict[str, str]:
    """The version each versioned manifest declares, by the same rule as `VERSIONED`."""
    entries = [
        as_object(entry)
        for entry in as_list(files[".claude-plugin/marketplace.json"]["plugins"])
        if as_object(entry)["name"] == MARKETPLACE_ENTRY
    ]
    assert len(entries) == 1, entries
    return {
        ".claude-plugin/plugin.json": as_str(files[".claude-plugin/plugin.json"]["version"]),
        ".claude-plugin/marketplace.json": as_str(entries[0]["version"]),
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


class ReleaseRunTests(unittest.TestCase):
    """Runs the pinned release-please on this repository's config and checks what it would write."""

    install: Path
    scratch: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        node, npm = shutil.which("node"), shutil.which("npm")
        if node is None or npm is None:
            raise unittest.SkipTest("node and npm are needed to run the pinned release-please")
        cls.scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.install = Path(cls.scratch.name)
        subprocess.run(
            [npm, "install", "--prefix", str(cls.install), "--no-audit", "--no-fund",
             "--ignore-scripts", f"release-please@{RELEASE_PLEASE}"],
            check=True, capture_output=True, text=True, timeout=300,
        )

    def release(self, version: str) -> dict[str, dict[str, object]]:
        done = subprocess.run(
            ["node", str(BUMP), str(self.install), str(ROOT), version],
            check=True, capture_output=True, text=True, timeout=120,
        )
        written = parse(done.stdout)
        return {path: parse(as_str(content)) for path, content in written.items()}

    def test_a_release_bumps_all_three_manifests(self) -> None:
        files = self.release("9.9.9")
        self.assertEqual(sorted(files), sorted(VERSIONED))
        self.assertEqual(versions(files), dict.fromkeys(VERSIONED, "9.9.9"))

    def test_a_release_changes_only_the_version_fields(self) -> None:
        files = self.release("9.9.9")
        for relative in VERSIONED:
            with self.subTest(manifest=relative):
                before = load(relative)
                after = files[relative]
                self.assertEqual(before.keys(), after.keys())
                if relative == ".claude-plugin/marketplace.json":
                    self.assertEqual(
                        {k: v for k, v in before.items() if k != "plugins"},
                        {k: v for k, v in after.items() if k != "plugins"},
                    )
                else:
                    self.assertEqual(
                        {k: v for k, v in before.items() if k != "version"},
                        {k: v for k, v in after.items() if k != "version"},
                    )


class ManifestPathTests(unittest.TestCase):
    def test_claude_plugin_agents_exist(self) -> None:
        agents = as_list(load(".claude-plugin/plugin.json")["agents"])
        self.assertTrue(agents)
        for relative in map(as_str, agents):
            with self.subTest(agent=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_marketplace_plugin_source_is_the_repository_root(self) -> None:
        marketplace = load(".claude-plugin/marketplace.json")
        plugin = as_object(as_list(marketplace["plugins"])[0])
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
