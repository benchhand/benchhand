# Manufacturing-aware fits and stacks

A fit designation states a desired dimensional relationship. It does not state process capability. Use `fits.py` for published ISO-table ranges and limits. The table does not qualify a process. Keep desired requirements, capability, as-manufactured measurement, post-finish dimension and assembled result separate.

For each critical interface, identify requirement, part/occurrence/component/feature endpoints, datum, function, fit, tolerance, dimension state, measurement method and evidence. Reconcile finish, coating, adhesive, joining distortion and assembly variation where relevant. Machine resolution and general-tolerance labels do not verify process tolerance. Unknown capability or contributors remain unknown.

Use worst-case intervals when collision, binding, interference, retention or lost function matters. Report nominal clearance separately from minimum remaining clearance. Include manufacturing, assembly, thermal, moisture, wear and deformation contributors when relevant. Do not count finish effects twice. Evaluate resulting dimensions in the declared after-finish state using qualified limits.

RSS cannot hide forbidden collision or worst-case failure. Statistical acceptance requires supported centered distributions, measured sigma or justified conversion, sample/coverage assumptions, independence or modeled correlation, suitable probability and approval. When those conditions or calculations are missing, report worst-case only and list statistics as an open question. Do not label legacy quadrature as probability.

Assembly checks include install/removal before closure, pins/bearings and closed-loop parts, fastener engagement/retention, tool/holder access, torque paths, adjustment, inspection, maintenance and disassembly. A final pose or tool name alone does not show access. Report sampled-path limits and distinguish geometric access from a real assembly trial.

## Fits without an ISO class

A fit in `hw/interfaces.yaml` (`limits.press_fit`, `running_fit` or `locating_fit`) takes exactly one form. The first is an ISO designation, `fit: "22 H7/p6"`. The second is explicit limits: `nominal_mm`, a `source` and one of these keys:

- `deviations: {units, hole: {upper, lower}, shaft: {upper, lower}}`: the limit deviations of each side from `nominal_mm`.
- `interference: {min, max, units}`: the band of interference on the diameter. Interference from 0 to 1.013 mm is a diametral clearance from -1.013 to 0 mm.
- `clearance: {min, max, units}`: the band of clearance on the diameter.

The `source` is a cad-record source. Design intent is kind `decision` (with its DR) or `user` (with its origin). A datasheet or guideline needs its own reference fields. `fits.py` `declared_fit` classifies every form by the ISO rule. A fit is interference when its largest clearance is at most 0, clearance when its smallest is at least 0, and transition otherwise.

`compliance: crush_rib` (optional `ribs: {count, width_mm}`) marks the interference as designed deformation of the compliant part. Its limits must reach some interference: an interference band, or deviations whose smallest clearance is below 0. Deviations that span clearance and interference classify as `transition` and still count as compliant. The fit form never blocks a gate:

- `cad-model check` allows overlap up to the stated maximum. It measures the diametral depth of the shared volume, and a model drawn deeper than the maximum is a `compliant_fit_overlap_exceeded` finding. When the depth is not measured, the row in `compliant_fits` is unknown and the interference check abstains: its status is `unknown`, it is listed under the result's `abstentions`, and it never passes and never blocks.
- Rigid press-fit maths does not apply. This covers stress, press force and the stack's seat qualification (`stack.press_fit_unknown`). Both `cad-model check` (`compliant_fits[].rigid_press_fit`) and `stackup.py` (`stack.press_fit_not_applicable`) report it as `not_applicable` with the reason.
- Retention still follows the joint's own source rule.

A fit entry that no form reads is a `validate.py` finding that names the field and the accepted forms. Examples are a plain-language string, a misspelled key, or a list where a unit or a compliance belongs. `cad-model check` reports it as `fit_invalid`, so the check exits 1, never 2. An invalid fit allows no overlap and waives nothing. `access.py` grants it no engagement exemption and lists it under `detail.invalid_fits`. `stackup.py` keeps the seat `stack.press_fit_unknown`. An ISO designation may separate its size from its classes with any whitespace, such as a tab.

Drawings print explicit limits, not an ISO class. A part drawing prints deviations as limits on the size. It prints a band beside the modelled size, for example `21.4 (interference 0/1.013, crush ribs)`. An assembly note states the fit as `fit ⌀22 interference 0/1.013, crush ribs`. A stack contributor can take its bounds from explicit limits with `fit: {interface, kind[, side]}`.
