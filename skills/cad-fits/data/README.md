# ISO 286 data provenance

`iso286.json` holds the tables `scripts/fits.py` reads. Every value is in micrometres.

## Source and edition

| Table in `iso286.json` | Standard, edition, table |
|---|---|
| `it` (IT6–IT11) | ISO 286-1:2010, 2nd edition (2010-04-15), Table 1. ISO 286-2:2010, 2nd edition (2010-06-15), Table 1 repeats it. |
| `shaft_es` (c, d, f, g, h) | ISO 286-1:2010 Table 4, upper deviation es. |
| `shaft_ei` (k, m, n, p, s) | ISO 286-1:2010 Table 5, lower deviation ei. The k values split into IT4–IT7 and "up to IT3 or above IT7". |
| js (computed, no table) | ISO 286-1:2010 clause 4.1.2.5 Note 3 and Figure 9: es = +IT/2, ei = −IT/2. ISO 286-2:2010 Table 23 prints odd-IT half-widths as fractional values (js7 over 18 to 30 mm = ±10.5 µm), so the script keeps half-micrometre precision. The whole-micrometre rounding option for js7–js11 comes from ISO 286-1:1988 (IS 919-1:1993 clause 9.3 note 21). It is not applied. |
| H hole (computed) | EI = 0, ES = IT (ISO 286-1:2010 Figure 8). |

Catalogue records: [ISO 286-1:2010](https://www.iso.org/standard/45975.html),
[ISO 286-2:2010](https://www.iso.org/standard/54915.html). Values were transcribed
from public previews of those editions and cross-checked against independent
transcriptions. All sources agree for every cell; no disagreement was found.

- ISO 286-1:2010 English preview: <https://cdn.standards.iteh.ai/samples/45975/c0a5df14167f48eebc6c17dc4da5f9c4/ISO-286-1-2010.pdf>,
  <https://preview.sist.si/sist-preview/20892/80af64ba32464e03bca9b1aff44366f3/SIST-EN-ISO-286-1-2010.pdf>
- ISO 286-2:2010 preview (Tables 1, 17–28): <https://preview.sist.si/sist-preview/54915/7d6147c29fed4e31af36b60561e88752/ISO-286-2-2010.pdf>
- IT cross-checks: Strobel <https://strobel-industry.de/en/wissen/toleranzrechner>,
  Dalloway <https://dalloways.com/charts/iso-tolerance-chart>,
  Manufacturing Processes <https://manufacturingprocesses.org/charts/iso-286-fits-tolerances>
- Shaft deviation cross-checks: RoyMech <https://www.roymech.co.uk/Useful_Tables/ISO_Tolerances/ISO_286_2s.html> (g, h, k, m, n, p),
  MetricMech <https://metricmech.com/reference/iso-286-fits-chart> (g, k, m, n, p, with subranges)

A controlled copy of the standard governs. These previews are not substitutes for one.

## Coverage

- Nominal sizes above 0 up to and including 120 mm. The IT ranges are ≤3, 3–6, 6–10,
  10–18, 18–30, 30–50, 50–80 and 80–120. The deviation ranges also split
  30–40, 40–50, 50–65, 65–80, 80–100 and 100–120. A size belongs to the first range
  whose upper bound it does not exceed, so 30 mm is in the 18–30 range.
- Grades IT6 to IT11.
- Hole H. Shafts c, d, f, g, h, js, k, m, n, p, s.
- Every hole/shaft combination in that set is computed with the ISO 286-1 rules.
  ISO 286-2 tabulates only a subset (for example c starts at c8). Combinations it
  does not tabulate are still valid ISO 286-1 classes.

## Not covered

- Sizes above 120 mm, grades outside IT6–IT11, holes other than H (shaft-basis
  systems need the hole Δ corrections in ISO 286-1 Table 3), and shaft letters
  other than those listed. Each of these exits 2.
- ASME B4.1 (inch preferred limits and fits). ASME sells the standard
  ([B4.1-1967 R2020](https://www.asme.org/codes-standards/find-codes-standards/preferred-limits-and-fits-for-cylindrical-parts)).
  The only public tables found are secondary reproductions of the 1967 text, so
  no B4.1 table ships here.

# Commodity hardware data

`commodity.json` holds the modelled dimensions of common commodity hardware, in millimetres, so a
model or a record reads one table instead of re-deriving thread and head sizes. `scripts/commodity.py`
is its lookup. Values are nominal or maximum catalogue values for modelling, not inspection limits.
Each block of the file carries its own `source`; this table adds the checking notes. All values were
checked on 2026-11-24 against the public pages named here, not against controlled copies of the standards.

| Block | Source | Checked |
|---|---|---|
| `thread.pitch` | ISO 261 coarse pitch, in the ISO 4762 table at <https://www.fasteners.eu/standards/ISO/4762/>. | All seven rows. |
| `thread.clearance_hole_medium` | ISO 273:1979 medium series (H13), from IS 1821:1987 at <https://forgent3d.com/en/calculators/clearance-hole-chart>, matched by <https://mechcodex.com/reference/metric-clearance-hole-sizes>. | All seven rows agree. |
| `thread.tap_drill` | DIN 336 / ISO 2306 coarse thread, <https://mvg-frankfurt.de/en/calculators/tap-drill>, matched by three further tap drill charts. | All seven rows agree. |
| ISO 4762 | <https://www.fasteners.eu/standards/ISO/4762/>: `dk` max, `k` max, `s` nominal. | All seven rows agree. |
| ISO 7380-1 | <https://www.fasteners.eu/standards/iso/7380> and Table 1 of the ISO 7380-1:2022 preview at <https://preview.sist.si/sist-preview/78699/a175805085534f98983d6c8aa583a5b0/ISO-7380-1-2022.pdf>. The standard starts at M3, so the M2 and M2.5 rows of an earlier draft were removed. | M3 to M8 agree. |
| ISO 4032 | <https://www.fasteners.eu/standards/ISO/4032/>: `s` nominal and `m` max. | All seven rows agree. |
| ISO 7089 | <https://www.fasteners.eu/standards/ISO/7089/>, which prints the DIN 125 form A table with the same dimensions: `d1` min, `d2` max, `h` between its min and max. | All seven rows agree. |
| ISO 8734 | <https://www.fasteners.eu/standards/ISO/8734/>: diameters 2, 3, 4, 5, 6 and 8 mm, tolerance m6. | All six rows. |
| `heat_set_insert` | No standard exists. CNC Kitchen standard-length brass inserts (store table of 2026-04-10), as listed at <https://forgent3d.com/en/calculators/clearance-hole-chart>. | Makers differ, see below. |

Heat-set insert spread between makers, for the recommended hole: M4 5.7 mm (CNC Kitchen) against 5.6 (ruthex)
and 5.67 (PEM IUTB); M5 6.5 against 6.4 and 6.43; M6 8.1 against 8.0 and 8.03; M8 9.7 against 9.6.
ruthex lists the M2.5 standard insert at 5.7 mm long where CNC Kitchen lists 4.0 mm. Use the datasheet of the insert bought.
