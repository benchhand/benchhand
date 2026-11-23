# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every marketplace and package manifest stays at the release version.

release-please (type `simple`) bumps only CHANGELOG.md and `.release-please-manifest.json`. The
`extra-files` entries in `release-please-config.json` make a release PR bump the other manifests:
the Claude plugin and marketplace files (which Codex also reads, as its legacy-compatible
marketplace) and the root `package.json` that pi installs. These tests fail when a manifest is left
out of `extra-files`, or when a version drifts from the release manifest.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "release-please-config.json"
RELEASE_MANIFEST = ROOT / ".release-please-manifest.json"

# The manifests a release must bump, each with the JSONPath of its version. The list is the policy;
# the config under test has to match it exactly.
VERSIONED = {
    ".claude-plugin/plugin.json": "$.version",
    ".claude-plugin/marketplace.json": "$.plugins[0].version",
    "package.json": "$.version",
}
STEP = re.compile(r"\.(\w+)|\[(\d+)\]")


def load(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def at(document: Any, path: str) -> Any:
    """Value at a simple JSONPath (`$.key`, `[n]` steps only, the forms release-please needs here)."""
    rest = path[1:]
    if not path.startswith("$") or STEP.sub("", rest):
        raise ValueError(f"unsupported JSONPath {path}")
    value = document
    for key, index in STEP.findall(rest):
        value = value[int(index)] if index else value[key]
    return value


def extra_files() -> dict[str, str]:
    package = load("release-please-config.json")["packages"]["."]
    entries = package.get("extra-files", [])
    assert all(entry.get("type") == "json" for entry in entries), entries
    return {entry["path"]: entry["jsonpath"] for entry in entries}


class ReleaseVersionTests(unittest.TestCase):
    def test_config_bumps_exactly_the_versioned_manifests(self) -> None:
        self.assertEqual(extra_files(), VERSIONED)

    def test_every_manifest_version_equals_the_release_manifest(self) -> None:
        release = load(".release-please-manifest.json")["."]
        for relative, path in sorted(VERSIONED.items()):
            with self.subTest(manifest=relative):
                self.assertEqual(at(load(relative), path), release)

    def test_the_jsonpath_reader_follows_keys_and_indexes(self) -> None:
        document = {"a": {"b": [{"c": "x"}, {"c": "y"}]}}
        self.assertEqual(at(document, "$.a.b[1].c"), "y")
        with self.assertRaises(ValueError):
            at(document, "$..c")


class ManifestPathTests(unittest.TestCase):
    def test_claude_plugin_agents_exist(self) -> None:
        agents = load(".claude-plugin/plugin.json")["agents"]
        self.assertTrue(agents)
        for relative in agents:
            with self.subTest(agent=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_marketplace_plugin_source_is_the_repository_root(self) -> None:
        marketplace = load(".claude-plugin/marketplace.json")
        plugin = marketplace["plugins"][0]
        self.assertEqual(plugin["name"], load(".claude-plugin/plugin.json")["name"])
        self.assertEqual((ROOT / plugin["source"]).resolve(), ROOT)

    def test_pi_package_declares_the_skills_directory_and_the_gallery_keyword(self) -> None:
        package = load("package.json")
        self.assertEqual(package["name"], "benchhand")
        self.assertIn("pi-package", package["keywords"])
        self.assertEqual(package["pi"]["skills"], ["./skills"])
        self.assertTrue((ROOT / "skills/cad-flow/SKILL.md").is_file())

    def test_every_path_the_pi_package_declares_exists(self) -> None:
        for kind, paths in load("package.json")["pi"].items():
            for relative in paths:
                with self.subTest(kind=kind, path=relative):
                    self.assertTrue((ROOT / relative).exists())


if __name__ == "__main__":
    unittest.main()
