# Fidelity ladder and detailing rules

Load at every L1 LAYOUT, L2 PARTS, or L3 DETAIL commit batch. Also load before gates from L2 onward.

## Ladder

| Level | Adds | Typical features |
|---|---|---|
| F0 massing | Envelopes and joint frames only | Boxes, cylinders, concept primitives; L0 CONCEPT and L1 LAYOUT |
| F1 functional | Real interfaces | Pin bores at the `cad-fits` class, bosses, mating faces, process wall thickness, ISO 273 fastener clearance, counterbores sized to ISO 4762 heads (dk + 1 mm, k + 0.5 mm) |
| F2 form | Product form language | Tapered, lofted, or swept links, one exterior fillet radius family per part, bore-entry chamfers, ribs or pockets for stiffness and mass, a cable channel, a base ballast cavity, a revolved shade |
| F3 detail | Cosmetic and finishing detail | Engraved or embossed text, knurl or grip flutes, split lines, purchased parts as bought (real heads, sockets, retaining-ring grooves), warehouse fasteners in the assembly |

A level includes all earlier levels. Record the level a part reached in its batch note. When interfaces change, that part returns to F1 until the new interface is re-detailed.

## Gate minimums

| Stage gate | Every made part | Purchased parts |
|---|---|---|
| VARIANT gate | F0 | not modelled |
| LAYOUT freeze | F0 envelope, joint origins and axes from `assembly.yaml` | envelope |
| L2 PARTS gate | F2, `form_check.py --fidelity F2` exit 0 | F1: real shank, head envelope |
| L3 DETAIL gate | F3, `form_check.py --fidelity F3` exit 0 | F3 as bought |
| BOM onward | no regression below F3 | no regression |

## Form language

- Derive a link outline from plan and side views, then intersect them. Taper toward the lighter end and sag or waist the span between joints.
- Keep outline kinks at different stations. Coincident kinks can make silhouette fillets fail in OCCT.
- Keep outlines tangent-continuous where silhouette fillets run. Leave a round on a tangent. Blend shallow kinks between straight runs (40 mm on the links). A fillet across a 2-5 degree kink leaves edges 0.03-0.14 mm out of tolerance and may cause `step_readback` volume drift near 1e-5.
- Fillet each silhouette edge with one radius per part. Leave tangent seams unfilleted.
- Model axisymmetric form (puck, boss, shade, cap, socket) with one revolved profile and profile arcs/lines for fillets and chamfers.
- Run cable channels along the underside. Stop them with the cutter's end form.
- Give two bores that meet in a T different radii. Equal radii can produce singular intersection points and unorientable faces in CadQuery 2.8.

## Illustrative process design language

These form examples do not define material/process combinations or universal limits. Use an example only when it suits approved part intent. For process obligations, read `process-checks.md`. Any PETG, 6061, nozzle, cutter or threshold choice needs part evidence and a capability profile. The examples do not set service needs or a default route.

### FDM example details

| Feature | Example only |
|---|---|
| Exterior edges | Consider edge rounds and bed-face relief when suitable to function and route. |
| Walls | Size by structural region and the profile's guideline minimum, not a universal thickness. When `CAD_CALIBRATION=1`, a declared calibrated profile replaces the guideline. |
| Overhangs | Plan orientation, bridges or reachable noncritical supports against route capability. |
| Holes | Use the functional fit requirement and measured process evidence; orientation affects finishing and inspection. |
| Threads | Compare qualified thread-forming geometry with purchased hardware/inserts and route capability. |
| Detail | Size engravings, flutes and grooves for the selected process and function. |

### CNC example details

| Feature | Example only |
|---|---|
| Internal corners | Relate radii to cutter, reach and setup. |
| Pockets | Check tool reach, stock, web, workholding and inspection. |
| Clevis slots | Check mating-part access, engagement and full joint range. |
| Pin bores | Specify fits from `cad-fits`; verify capability and finishing route. |
| Exterior edges | Draw functional edge and finish requirements with source and inspection method. |
| Channels | Check cutter/holder access and operation sequence. |

## Existing form diagnostics

`form_check.py` reports sampled minimum walls and `block_like`/oriented-box descriptors as geometric form diagnostics. These findings do not set a universal FDM threshold or set a process limit. A wall sample can identify a region for review. Sampling limits apply; consult the command result and implementation. The build-envelope check reports geometric fit. It does not evaluate a process route. The route-check producer owns process pass/fail criteria.

## Joint mating geometry

From F1, `form_check.py` checks each joint end for the geometry that mates it. The part's geometry counts, not its sourcing. A made, bought or existing part passes with a bore or a shaft on the joint axis. The cylinder axis is parallel to the joint axis and passes within `--joint-tol` (0.05 mm) of the joint origin.

Adjoining faces on one axis line with one radius count as one cylinder, however the kernel splits the surface. An edge fillet rounds a corner, so it never counts. The check finds one as a convex cylinder that meets two or more distinct surfaces tangentially, none of them on its own axis. Fragments of one plane or one cylinder count as one surface. A partial journal still counts: a half bore, a D-shaft, or a shaft between coaxial shoulder fillets.

Of the cylinders on the axis, the check names the bore and shaft pair with the closest radii. A roller on a stub axle therefore reports its bore. The joint's `parts` row gives `kind` (`bore` or `shank`), `radius` and `offset_mm`.

A fixed joint between parts mounted flat, such as a head bolted to a deck, may have no cylinder on its axis. Such a joint passes by face contact instead. The check splits each overlap into connected patches and judges each patch on its own. A patch needs:

- one planar face on each part, with normals opposite to within 1e-4 rad
- an overlap of at least 0.01 mm² once the child face is laid on the parent face's plane
- a separation within `--joint-tol` across the whole patch, so a slight tilt counts at the patch's far edge
- the joint's datum plane, through the joint origin and normal to the joint axis, meeting the patch
- a locator that ties the patch to the joint

The locator is the first of these that applies:

1. `mount`: a declared `mount: {point: [x, y, z]}` on the joint, in assembly mm, must lie on the patch. The datum plane check still applies to that patch.
2. `origin`: on a face-normal joint, its axis along the contact normal, the joint origin must lie on the patch.
3. `projection`: on an offset joint, the origin's projection along the contact normal must fall inside the patch's outline. It may sit in an opening of the patch, such as a head's bore, at most 20 mm from the contact.

The 20 mm allowance covers a bore or bolt opening up to Ø40 in a mounting face. A V6 head's cylinder opening puts the projection 13 mm from its contact. In a wider opening the contact is only a rim around the point, and the geometry cannot tie it to the joint. Such a mount declares `mount.point`.

A V6 head joint is an offset joint: its frame sits on the crank axis, with its axis along the deck. The origin projects onto the deck inside the head's gasket outline. Each value must be within `--joint-tol`, compared before rounding. A face pair is skipped only when the child face's bounding box lies wholly more than 1 mm from the parent plane.

The row gives `kind: face` and these values:

- `offset_mm`, the largest separation
- `area_mm2`, the patch area
- `datum_mm`, the distance from the datum plane, 0 when the plane meets the patch
- `locator` and `locator_mm`, the rule used and how far the point lies from the patch or outline
- `opening_mm`, for a projection only: how far the projected point lies from the contact itself

Revolute and prismatic joints still need a cylinder.

An end with neither is a blocking `joint_bore_missing` finding. Its `nearest` field holds the closest cylinder on the axis. On a fixed joint, its `contact` field holds the closest facing patch within 1 mm, or null.

A face contact can also be unknown. Its blocking `joint_contact_unknown` finding has `status: unknown` and a `reason`, and the end's row reads `kind: unknown`. This happens in two cases:

- The kernel cannot compute an overlap. The check reads the error from cad-model's `occ.boolean`.
- A patch meets the datum plane, but only its locator misses, and the joint declares no `mount`. The geometry alone cannot tell the intended mount from incidental contact. The finding's `remedy` asks for a `mount.point` on the contact.

## Render self-check: appearance only

- Samples: each face gets a uniform UV grid. Where the surface stretches most, samples are at most half the minimum apart, with at most 4096 per face (`capped_faces` counts faces sampled coarser). The grid covers the face's full UV range, so a bore seam does not determine sample locations. Samples within four B-rep tolerances (at least 0.1 µm) of a trim boundary are skipped. A seam is not a boundary.
- Chord: each sample casts a ray along its inward normal to the first exit. The exit counts only when its face normal is within 20° of antiparallel. Two faces at an acute edge therefore do not read as a wall. `tapered` counts samples whose exit failed this test.
- Persistence: a chord below the minimum counts only when the wall continues for at least its own thickness. Along both UV directions, the face's own sample a chord-length away must leave through the same exit face (at up to twice the minimum thickness) or through another opposing face below the minimum. A bore web thinnest at one point still counts. Where that point leaves the face, a parallel ray stands in. Relief such as lands between engraved letters ends at its own edge and counts in `shallow`. The thinnest such chord appears in `shallow_thickness_mm` and `shallow_location_mm`.
- Report: `thickness_mm`, `location_mm` (sample point), `faces`, `faces_sampled`, `samples`, `measured`, `tapered`, `shallow`, `capped_faces`, `spacing_mm` and `seconds`. A ray stops at the running minimum. `measured` counts samples that bound the wall at or above it. `tapered` and `shallow` count only exits shorter than the minimum. No measured wall gives a `min_wall_unmeasured` finding, never a pass.

Limits: the minimum is sampled, not certified. A curved wall reads slightly thick (a 0.8 mm web beside a Ø10 bore reads 0.81 mm). Sampling may miss thin regions smaller than spacing on a capped face. A taper whose faces are over 20° from antiparallel is not measured and appears in `tapered`. Shortest-distance measurement would also call acute edges and chamfers thin walls. Kernel tolerance is 1e-7 mm; rays start 1e-5 mm inside the face.

## Render self-check: block-like appearance

`block_like` is an appearance heuristic. It applies only to `form_class: solid` in `part.yaml`; absent means `solid`. Frame, plate, skeleton and shell are exempt. The oriented-box checks are:

1. At least 80% of face area is planar and normal to a principal axis, or the part has no recognized blend/freeform faces.
2. Solid material occupies at least 20% of oriented-box volume.
3. The shortest oriented-box extent exceeds 10% of the second-shortest extent.

These measures catch solid placeholder forms and exempt thin open frames/sheet-like parts. They do not judge a process route.

Before the gate, answer these by looking at a render:

- Does any made part still read as its F0 primitive from three sides?
- Does the outline change section between joints, or stay a constant-section bar?
- Are exterior edges blended in one radius family, with bores chamfered?
- Does each pocket, rib or channel have a structural or routing reason?
- Do purchased parts show their bought form (head, socket, groove), not a bare cylinder?

A part that reads as an F0 primitive or misses a form question stays in the current stage.
