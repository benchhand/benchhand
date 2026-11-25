# Release procedure

Before release, build record-derived guides for the frozen version and proposed revision. Run from the project root; use the UTC date that `release.py` will seal:

```sh
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/build_guide.py hw --version vN --rev A --date YYYY-MM-DD --out hw/docs
```

If this command refuses because a guide was not generated or was edited since generation, review its contents and rerun with `--replace` only when replacing those edits is intended. Then validate and issue the release after explicit RELEASE-gate approval recorded in `hw/decisions/DR-###-slug.md`:

```sh
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/validate.py hw
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/release.py hw --rev A --version vN --decision DR-###
```

`validate.py`: exit 0 has no error findings, exit 1 has blocking findings, exit 2 is invalid input or infrastructure failure. `release.py`: exit 0 sealed the release or found it already sealed, exit 1 refused it with findings and wrote nothing outside `.cad-work/`, exit 2 is invalid input or infrastructure failure. The run rewrites a used `hw/releases/<rev>/` only to repair a stale package of the same frozen manifest, and an identical rerun is a no-op. There is no `--preview` flag, and a refused run is not a successful dry run.

Releasing the same revision again is idempotent. When the package verifies and its frozen manifest sha equals the current one, the run exits 0 and changes nothing. A different frozen manifest under that revision needs a new revision, and the run refuses it.

A package that fails its seal is stale. The run acts on what the stale manifest names:

- The same version, approval and frozen manifest sha: the run repairs the package. It seals it again with the original release date, so the okh.toml and build guide from that day stay valid.
- Another version, approval or frozen manifest: the run refuses it. Automatic repair is not supported there. Use a new revision.

The run holds the revision's lock from start to end, from the first check of the package to the claim. A second run of the same revision waits, then checks what the first run left. During a repair the run moves the old package to `.cad-work/replaced/` and claims the new one. When the claim fails, the run puts the old package back, except where another writer put a package. A run with `--prune` holds the same lock and refuses a stale package. Run the release without `--prune` first.

Record v2 release writes **manifest schema v3**. It copies the frozen assembly definition and record snapshot, specification artifacts referenced by purchased parts, manufacturing outputs for each custom-made PN, every declared part or assembly drawing, every analysis bundle for a case under `hw/analysis/cases/<case>/`, every bundle cited by `verification.csv`, the current BOM and verification matrix, docs, and the acceptance inventory under `acceptance/`. Check, drawing, motion, render and capture bundle route kinds and manufacturing output files are defined by the generated canonical [layout table](layout.md); a route with no output contract blocks release. All supported process kinds' files go under `manufacturing/<PN>/`, not into separate per-process package folders.

The package holds the delivered product only. `release.py` asks cad-record `sourcing.delivered` and leaves existing equipment (`make_buy: existing`) out of the definition, the BOM and the manufacturing outputs:

- `definition/product.step` is the version's `product.step`. When a product name in it starts with the PN of existing equipment, the release refuses it (`release.contaminated`). It also refuses a version that has existing equipment but no `product.step` (`release.missing`).
- With existing equipment, `context/assembly.step` holds the in-context scene for fit review.
- `mates_with` lists each interface with an existing end. An entry names the interface and its type, the product-side end, and the equipment's PN, title and `existing` record.
- A version built before `product.step`, and without existing equipment, ships `definition/assembly.step` as before.
- `bom.csv` must not list existing equipment.

`flow.frozen` must name vN, the version manifest's `manifest_sha256`, the output of `evidence.py digest --hw hw --version vN` in `evidence_sha256`, and the approving DESIGN-freeze decision. The release approval is a separate DR passed as `--decision`. Required evidence and selection must pass the release gate; unknown never passes. See [evidence.md](evidence.md) for the acceptance inventory and [manufacturing-intent.md](manufacturing-intent.md) for record policy.

To plan pruning after release, run the release command above with `--prune`; it prints a digest-bound plan without removing anything. Review `keep` reasons and the `remove` list, then apply only with the exact digest printed by the plan:

```sh
uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-record/scripts/release.py hw --rev A --version vN --decision DR-### --prune --yes <64-character-digest>
```

Only versions older than the released version are eligible. The plan retains released/latest/frozen/change-baseline versions, versions named by decisions and versions referenced by surviving manifests. A stale plan digest is refused.