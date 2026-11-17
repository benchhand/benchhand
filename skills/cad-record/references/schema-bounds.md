# Schema bounds, defaults and facts

The JSON schemas in `schemas/` are the source of truth for every tunable number. Read the bounds and defaults from them. Flags and variables live there too. Do not copy any of it into code.

## Facts and tunables

A fact describes a real material, machine or part. It has no default and stays UNKNOWN until sourced.

- Build volume
- Nozzle diameter
- Young's modulus
- A measured calibration

A tunable is a setting the user may change. It always has a `default`.

- Clearance margin
- STL mesh deflection
- FEA timeout
- Motion frame rate

The build volume has no default. It comes from the active capability profile's envelope.

## Annotations

- `default`: the value used when the field is absent.
- `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `enum`: the hard bound. A violation is an error.
- `x-plausible`: the band of normal values, as `min` and `max`. A miss is the warning `plausibility.<field>`. Only an error blocks. The band may name `relative_to`, a field it scales with.
- `x-cli`: the command-line flag, as the code reads it.
- `x-env`: the environment variable, as the code reads it.
- `x-unit`: the unit of a field whose name carries none.

A quantity states its unit. A hard bound on a fact holds in every unit of its dimension.

`tests/test_schema_bounds.py` holds the expected bounds, bands and defaults.
