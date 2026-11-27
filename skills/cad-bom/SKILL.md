---
name: cad-bom
description: Rolls up an engineering BOM from a STEP AP242 assembly and hw/parts metadata into bom.csv. Triggers on "build the BOM", "EBOM", "bill of materials".
user-invocable: false
---

Resolve `$CAD_SKILLS` as the parent directory of this skill's directory; it contains the installed `cad-*` skill folders.

# CAD EBOM Roll-up

Stage BOM, after L3 DETAIL; RECHECK reruns it from LAYOUT freeze onward.

TRIGGER
+ "build the BOM", "update bom.csv", "how many pins does the assembly use"
+ a STEP assembly or a `hw/parts/<PN>/part.yaml` changed after LAYOUT freeze
- a manufacturing BOM, purchasing list, or cost roll-up → out of scope; this skill emits the EBOM only
- the BOM skeleton during L1 LAYOUT, before a STEP exists → `cad-record`

## Workflow

1. Export the assembly as STEP AP242 with product names that start with the PN
   (`PIN-0001` or `PIN-0001 Dowel pin`).
2. `uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-bom/scripts/bom.py ASSEMBLY.step --hw hw [--version vN|latest]` writes `hw/bom.csv` by default and prints JSON summary fields `status`, `rows`, `total_qty`, `missing_part_yaml`, `missing_in_step`, `unidentified`, `invalid_designation` (schema: `cad-record/schemas/bom-summary.schema.json`). With `--version`, `reconciled_against` names the selected version. The command reads metadata from that version's snapshot.
3. Exit 1 means the record and the model disagree; bom.csv is still written. Resolve each list:
   `missing_part_yaml` → create the part.yaml or rename the product;
   `missing_in_step` → remove the stale part or add it to the model;
   `unidentified` → give the leaf product a PN;
   `invalid_designation` → each item names the PN, the `purchase.designation.<field>` (or `purchase.designation` for a value that is no mapping) and the reason (a blank field, a size that embeds the length, a length that is not a finite positive number, a property class the YAML reads as a number): correct that field in the part.yaml, quoting `"8.8"`. The row stays in the BOM with its designation column empty until the field is valid.
4. Rerun until exit 0, then COMMIT records the bom.csv change with the stage.
   Without `--version`, the current `hw/parts` records are reconciled; with it, BOM metadata and reconciliation use the selected version's snapshot, not current records.

## Semantics

- `level` 0 is a numbered top assembly; each child is parent + 1. Children of an unnumbered top assembly start at 1. The record validator uses the same rule, including accepting a numbered assembly row without `part.yaml` when it has child rows.
- `qty` counts instances per one parent. A repeated subassembly is one row with its qty; its children appear once beneath it. `total_qty` in the JSON multiplies through.
- An assembly product without a PN is a phantom group; its children merge into the nearest numbered parent.
- Existing equipment (`make_buy: existing`) gets no row at any level, the root included, and its part.yaml is never `missing_in_step`. Its children merge like a phantom's. `product.step` and `assembly.step` give the same BOM.
- Assemblies need no part.yaml; leaf parts do. `mass` copies `mass_target` (grams).
- `unit` is `pcs`: `qty` counts STEP instances. `designation`, `manufacturer`,
  `manufacturer_part_number` and `supplier_part_number` copy part.yaml `procurement`;
  `specification_ref` and `specification_rev` copy its `specification` `reference` and `revision`.
  They stay blank when the part declares none.
- A commodity item (screw, nut, washer, dowel, O-ring, heat-set insert) is bought by designation:
  the structured `purchase.designation` (or `procurement.designation` text) states the standard, size, length, property class and finish
  (`ISO 4762 M4x20, 8.8, A2`; an insert is a family designation: thread, length, outside diameter, hole).
  Its manufacturer and part-number cells stay blank and the supplier is any supplier. A
  `purchase.reference_catalogue` entry (vendor, PN, URL) is a non-binding example and never an order requirement.
  Record a vendor part number only when a requirement needs a property the designation cannot state (special
  coating, captive part, certified lot). The build guide merges commodity rows with one designation into a single
  buy row with its PNs and total quantity.
- Availability means a named supplier or commodity (any supplier). No lead time is required.

## Reading a source for a part

The lead runs `uv run --script "$CAD_SKILLS/cad-bom/scripts/source_extract.py" <PN> <URL or file> [--hw hw] [--find <text>]` for a datasheet or standards page that a sourcing researcher needs. It stores the file in `hw/parts/<PN>/sources/` with `<name>.source.json` (origin, sha256, access date), reads PDF text per page and HTML tables as full grids (colspan and rowspan expanded), and with `--find` prints each matching row with the column header above every cell. Record the sha256 where `part.yaml` cites the file. Exit 1 names a finding (`http_403`, `http_404`, `http_429`, `network_error`, `transfer_error`, `file_not_found`, `unreadable_file`, `unsupported_type`, `no_text_layer`, `no_tables`, `unsafe_name`, `symlink_refused`, `storage_error`, `sidecar_invalid`, `name_conflict`, `no_match`); exit 2 is bad arguments. A PDF match is a line only, marked `column_order: "unverified"`: check the column order against the header line before adopting a value. This is a read aid, not a roll-up check: it never changes `bom.csv`.

## Rules

MUST Treat exit 1 as a check failure that blocks the stage gate.
MUST Edit part metadata in `hw/parts/<PN>/part.yaml`, never in bom.csv; the script overwrites bom.csv.
NOT Count parts by hand or from a viewer; quantities come from the STEP product tree.

```text
L1 BOM: {PASS|FAIL}, {rows} rows, {distinct PNs} part numbers
   Mismatches -- {each non-empty list from the JSON summary}
```
