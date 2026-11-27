# Manufacturing evidence and findings

The versioned producer result is immutable evidence; authored ledgers do not rewrite it. The JSON schemas and `manufacturing_intent.py` are the field and policy authority.

## Independent evidence facets

Record each evidence claim by requirement or obligation, subject (product/part/component/operation/interface), version/manifest/geometry, method, criterion, source, conditions, dependencies, uncertainty, coverage and result. Levels are independent: `cad_valid`, `geometrically_manufacturable`, `process_planned`, `simulation_supported`, and `physically_validated`. A CAD-valid model does not imply geometric manufacturability; a geometric check does not imply a process plan; a plan does not prove process capability or successful manufacture; simulation is not physical validation. Do not promote a result between levels or scope by implication.

Do not claim actual toolpath or print success from geometry, dimensional capability from machine resolution or a general tolerance, fit from nominal CAD clearance, assembled access from the final pose, material strength from density/family/another material, fatigue/creep/wear/life from static linear FEA, a test outside its specimen/configuration/conditions, or regulatory certification from plugin checks. A planned test/coupon is an obligation, not passing physical evidence. A test supports only the tested configuration and scope.

Physical validation requires an as-built specimen/configuration, actual material and process state, appropriate protocol and setup, traceable calibrated instrument, results, date, scope, responsible approver and evidence bytes. Verification requirements remain criterion-specific. A status word or five-column verification row cannot override missing, stale, incomplete or inapplicable evidence.

## Finding and disposition

A finding names a stable code and ID, subject, severity (`critical|major|minor|info`), outcome, measured value/range and units or reason for UNKNOWN, criterion/operator/source and linked requirement, method and hash-bound evidence, uncertainty/assumptions/coverage, functional consequence, remedy and alternatives/tradeoffs, and recheck family/scope/inputs/earliest affected stage. Geometric findings are limited to the observation actually measured.

## Review verdicts

Reviewers use the same verdict derivation:

| Situation | Verdict |
|---|---|
| Any `critical` or `major` finding with outcome `fail` | `MAJOR` |
| A mandatory or safety-relevant obligation has outcome `unknown` | `MAJOR` |
| Only `minor` or `info` findings remain, or noncritical scope is explicitly untested | `MINOR` |
| No findings remain and every applicable in-scope check is evidenced | `PASS` |

The finding severity enum is defined above. Findings retain their own severity/outcome; the review verdict summarizes the whole assigned scope. Untested required checks are never PASS.

Producer packets remain immutable. `manufacturing/findings.yaml` contains dispositions tied to the exact producer bundle and manifest hash, status, reason, decision and any replacement evidence. Resolution requires newly applicable evidence; acceptance or decline does not convert FAIL/UNKNOWN to PASS. A waiver is scoped to subject, criterion, operating envelope, expiry or change, reason, consequence and approving authority; forbidden safety, regulatory and geometry failures cannot be waived. A permitted trial waiver remains explicitly `waived` and untested.
`evidence.py check --hw hw --version vN` assesses expected obligations from the current record, sealed check packets and accepted evidence ledger. `digest` computes the acceptance inventory digest used by the freeze anchor. Results distinguish pass, fail, waived, not applicable, stale, planned and unknown; none can be promoted by a CSV status. The gate packet includes selection status and digest. `gate.selection` blocks unsupported or contradictory choices, stale confirmations and, at design freeze/release, missing material/process selection. An approved confirmation is checked against its decision (`gate.selection_approval`). `impact.py --hw hw --since-version vM --version vN` reports changed fingerprint domains, stale obligations/evidence, reopened decisions, the earliest affected stage, and whether the existing selection input digest changed; it never passes results forward automatically. The recheck stamp recomputes the obligation and selection gates from the staged record. The stamp includes the selection evaluator, so `--passed` cannot mask a changed or unresolved decision.

When distinct checks share an anchor, the first keeps the plain id, so existing references still resolve. Each later one adds `~<critical surface id>` or `~<REQ>`: two critical surfaces on one feature give `feature.fit@PN#feature` and `feature.fit@PN#feature~<second surface>`. When no qualifier separates them, the unknown `intent.obligation_identity@<anchor>` names them, and no result can pass it. To separate them, name the requirement that each policy obligation serves. Keep their subjects and levels.

Evidence of a critical surface names the region it measured. A ledger entry for another region gets `evidence.subject`. An entry with no region gets `evidence.subject_ambiguous`, and neither counts. A stack-up judges the surface whose region its subject names. When two or more surfaces fit that subject, it reports `stack.obligation_ambiguous` and judges none of them.

Only a record change resolves an `intent.*` or `assembly.joint` obligation. `evidence.py check` reports a ledger entry for one as `evidence.record_derived`. The entry counts for nothing, whether it records a result or a waiver. Record the joint facts that `assembly_operations` checks: retention, edge distance and host temperature.

A record change can alter the expected checks while their ids stay the same. A region added to a critical surface does this. A packet or stamp made before the change cannot stamp the record (`evidence.expected_changed`). Regenerate the packet with `evidence.py check` and stamp again.

`evidence.py run --hw hw --version vN --scope commit|full [--stage S]` runs the producers that the scope needs. Then it prints the packet that `check` prints for the same inputs.

- The report follows `schemas/evidence-run.schema.json`.
- The full scope needs `--stage`, the stage of the gate it checks. A provisional approval has already moved `flow.stage` on. Without `--stage` the run is a usage error with exit 2.
- The commit scope uses `flow.stage` when `--stage` is absent.
- The run refuses a version that is not built or is stale, with exit 1. Build it again.

The run works in this order:

- It starts with `validate.py`.
- It runs the producer steps that `MATRIX` marks for the scope.
  - `commit` holds a batch's fast checks.
  - `full` holds the full check after the provisional gate.
- A step marked C runs only while an obligation that it covers is stale or open and due at the stage.
- The commit steps run in parallel through `cad-flow/scripts/run.py`. The full scope runs its steps one after another.
- The run reads the results in the fail-fast order. At the first blocking result it stops the producers that still run.
- A producer that exits 0 without a JSON report gave no result.

The freeze records `flow.frozen.evidence_sha256` from the evidence digest plus the approving decision. Release manifest v3 seals the acceptance inventory, evidence policy, material/process route and service scope/limits. Only release manifest v3 is read.

## Where cited files live

Evidence must be reproducible from the record. Each file an accepted entry cites lies inside the project root. Git tracks it, or it sits in a record-managed result: a sealed `hw/versions/<V>/` or a bundle such as `hw/checks/<V>/<S>/evidence/`.

The rule refuses these with `evidence.scratch_path`:

- scratch: `.cad-work/` and `hw/.cad/`
- temp directories and `~/`
- anything outside the root

The rule checks these fields:

- structured paths: `artifacts`, `bundle`, `source.artifact`
- ledger prose: `method`, `tool`, `producer`, `note`
- more ledger prose: `coverage`, `uncertainty`, `reason`
- nested prose: `physical.*`, `waiver.*`, `criterion.conditions`

A token is a path only with a path shape:

- a scratch prefix
- an absolute path
- a `..` segment
- a file extension after a `/`

A record result that the text cites but `artifacts` omit gets `evidence.uncited_artifact`.

To fix either code, promote the file into the record and list it in `artifacts` with its sha256. A good place is `hw/checks/<V>/<S>/evidence/`. Or cite a tracked packet.

## Freshness, impact and release claims

A prior result carries only when its exact subject, geometry/configuration, requirements, criterion, conditions, capability, sources and dependency fingerprints still apply. Missing dependency metadata is unknown and requires rerun or a documented assessed carry-forward into a current version; old bundles and seals are never edited. `impact.py --since-version vM` reports changes across use case, part/component geometry, route/orientation/material, loads, requirements, interfaces, profiles, capability, selection digest, evidence and decisions. It lists stale obligations and reopened decisions with earliest affected stage; it does not change the stamp threshold or automatically pass old results. A selection digest change requires a new assessment and confirmation through the approval gate. Re-evaluate from the earliest stage that accepted the changed fact.

Release claims state purpose, scope, operating limits, exclusions, required obligations, evidence levels and any approved trial status. Service class describes intended-use maturity and consequence. Values are `appearance_concept`, `fit_assembly`, `functional_demo`, `working_prototype`, `production` and `safety_critical`. Prototype is a service class. Increasingly consequential use needs evidence at the appropriate level. Unknown service blocks final selection and new manufacturing freeze/release. CAD and static simulation never certify physical performance or regulatory compliance.

`scripts/project_scope.py` holds the class rules. `RULES` says which obligations a release of each class must resolve. `SERVICE_POLICY` says which evidence levels the class may claim and whether it allows an unvalidated trial.

Evidence codes include `evidence.unqualified`, `.stale`, `.subject`, `.subject_ambiguous`, `.carry_forward`, `.csv_unsupported`, `.physical_claim`, `.expected_changed`, `.expected_invalid`, `.record_derived`; `packet.invalid`, `.unexpected`; `waiver.unapproved`, `.expired`, `.scope`, `.forbidden`; and `finding.critical`.
Gate/release codes include `gate.record`, `.fail`, `.stale`, `.unresolved`, `.use_case`, `.selection`, `.selection_approval`; `release.service_class`, `.scope`, `.trial_not_permitted`, `.waiver_forbidden`.
Selection codes include `selection.use_case_incomplete`, `.not_assessed`, `.candidate_eliminated`, `.recorded_route_eliminated`, `.primary_unknown`, `.candidate_unknown`, `.no_feasible_candidate`, `.evaluation_conflict`, `.evaluation_mismatch`, `.assessment_stale`, `.recorded_recommendation_eliminated`, `.recorded_recommendation_dropped`, `.final_unsupported`, `.recommendation_differs`. Reports carry subject, severity, outcome and evidence fields. Freeze and release block on missing candidates/selection.
