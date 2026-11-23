// SPDX-FileCopyrightText: 2026 Sjors Robroek
//
// SPDX-License-Identifier: MPL-2.0

// Runs the pinned release-please implementation on this repository's config and prints what a
// release to NEW_VERSION would write into each `extra-files` manifest, as JSON {path: new content}.
// Usage: node release_please_bump.cjs <release-please install dir> <repo root> <new version>
// It builds the same `simple` strategy the action builds from release-please-config.json and
// reads the file contents from the work tree instead of the GitHub API.

const fs = require("node:fs");
const path = require("node:path");

const [installDir, root, newVersion] = process.argv.slice(2);
const build = path.join(installDir, "node_modules", "release-please", "build", "src");
require(path.join(installDir, "node_modules", "release-please")); // index first: breaks a load cycle
const { Simple } = require(path.join(build, "strategies", "simple.js"));
const { Version } = require(path.join(build, "version.js"));

const config = JSON.parse(fs.readFileSync(path.join(root, "release-please-config.json"), "utf8"));
const rootPackage = config.packages["."];

const github = {
  findFilesByGlobAndRef: async (glob) => [glob],
};

async function main() {
  const strategy = new Simple({
    github,
    targetBranch: "main",
    path: ".",
    extraFiles: rootPackage["extra-files"],
  });
  const updates = await strategy.extraFileUpdates(Version.parse(newVersion), new Map(), undefined);
  const out = {};
  for (const update of updates) {
    const content = fs.readFileSync(path.join(root, update.path), "utf8");
    out[update.path] = update.updater.updateContent(content);
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exit(1);
});
