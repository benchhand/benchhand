"""Click options from a JSON Schema config, with the config loader's source reporting.

Load it by path, as `emit.py`. It imports Click only when it builds options. A field with `x-cli` becomes an
option, and `x-env` becomes its `envvar`, so Click checks the env var like the flag. The caller passes in the
loader module.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import click

CONFIG_INVALID = "config.invalid"
EXIT_USAGE = 2
FINDING_KEYS = ("message", "pointer", "value", "source")
EMIT_MODULE = "cad_record_emit"
VALUE_TYPES = frozenset({"integer", "number", "string"})


@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]
    schema: Mapping[str, Any]

    @property
    def declaration(self) -> str:
        return str(self.schema["x-cli"])

    @property
    def flag(self) -> str:
        return self.declaration.split("/")[0]

    @property
    def negative(self) -> str | None:
        """The second half of a paired boolean flag, `--no-x` in `--x/--no-x`."""
        return self.declaration.split("/")[1] if "/" in self.declaration else None

    @property
    def name(self) -> str:
        return self.flag.lstrip("-").replace("-", "_")

    @property
    def pointer(self) -> str:
        return "".join("/" + key.replace("~", "~0").replace("/", "~1") for key in self.path)


def fields(schema: Mapping[str, Any], path: tuple[str, ...] = ()) -> list[Field]:
    """Every field with an `x-cli` flag, nested ones included."""
    found: list[Field] = []
    for key, sub in schema.get("properties", {}).items():
        if "x-cli" in sub:
            found.append(Field((*path, key), sub))
        found += fields(sub, (*path, key))
    return found


def _bounds(sub: Mapping[str, Any], low: str, high: str) -> dict[str, Any]:
    lo, hi = sub.get(low, sub.get(f"exclusive{low.title()}")), sub.get(high, sub.get(f"exclusive{high.title()}"))
    return {"min": lo, "max": hi, "min_open": low not in sub and lo is not None,
            "max_open": high not in sub and hi is not None}


def _param_type(field: Field) -> click.ParamType:
    import click

    sub, kind = field.schema, field.schema.get("type")
    if "enum" in sub:
        if not all(isinstance(item, str) for item in sub["enum"]):
            raise ValueError(f"{field.flag}: only string enums are supported")
        return click.Choice(sub["enum"])
    if kind not in VALUE_TYPES:
        raise ValueError(f"{field.flag}: unsupported type {kind!r}")
    bounds = _bounds(sub, "minimum", "maximum")
    ranged = bounds["min"] is not None or bounds["max"] is not None
    if kind == "integer":
        return click.IntRange(**bounds) if ranged else click.INT
    if kind == "number":
        return click.FloatRange(**bounds) if ranged else click.FLOAT
    return click.STRING


def _option(field: Field) -> click.Option:
    import click

    common = {"default": field.schema.get("default"), "envvar": field.schema.get("x-env"),
              "help": field.schema.get("description")}
    if field.schema.get("type") == "boolean":
        if field.negative is None:
            common["default"] = field.schema.get("default", False)
        return click.Option([field.declaration, field.name], is_flag=True, **common)
    return click.Option([field.flag, field.name], type=_param_type(field), **common)


def _lookup(values: Mapping[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    node: Any = values
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return False, None
        node = node[key]
    return True, node


def _emit() -> ModuleType:
    if EMIT_MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(EMIT_MODULE, Path(__file__).with_name("emit.py"))
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[EMIT_MODULE] = module
        spec.loader.exec_module(module)
    return sys.modules[EMIT_MODULE]


def with_warnings(result: Mapping[str, Any], config: Any) -> dict[str, Any]:
    """`result` with the loader's warnings, unchanged, ahead of its own findings."""
    return {**result, "findings": [*config.warnings, *result.get("findings", [])]}


class SchemaCLI:
    """The options of one schema and the bridge to a loader module (`Layer`, `merge`, `ConfigError`)."""

    def __init__(self, schema: Mapping[str, Any], loader: Any, *, tool: str) -> None:
        self.fields = fields(schema)
        self.loader, self.tool = loader, tool
        flags = [field.flag for field in self.fields]
        if len(set(flags)) != len(flags):
            raise ValueError(f"duplicate x-cli flags: {sorted(flags)}")

    def needs_click(self, argv: Sequence[str], environ: Mapping[str, str]) -> bool:
        """Whether Click must run: `argv` has a schema flag or `--help`, or `environ` has an `x-env` variable.

        With nothing to check, a caller can skip the Click import and use the loaded config as it is."""
        flags = {"--help"}
        for field in self.fields:
            flags |= {flag for flag in (field.flag, field.negative) if flag}
        given = any(arg.split("=", 1)[0] in flags for arg in argv)
        return given or any(field.schema.get("x-env") in environ for field in self.fields)

    def params(self) -> list[click.Option]:
        return [_option(field) for field in self.fields]

    def default_map(self, config: Any) -> dict[str, Any]:
        """Map each parameter name to its loaded value, for the context `default_map`."""
        found = ((field, _lookup(config.values, field.path)) for field in self.fields)
        return {field.name: value for field, (present, value) in found if present}

    def layer(self, ctx: click.Context) -> Any:
        """Build a loader `Layer` from the values the user typed. Env and default values stay out."""
        import click

        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        for field in self.fields:
            if ctx.get_parameter_source(field.name) is not click.core.ParameterSource.COMMANDLINE:
                continue
            node = values
            for key in field.path[:-1]:
                node = node.setdefault(key, {})
            value = ctx.params[field.name]
            node[field.path[-1]] = value
            used = field.negative if field.negative and value is False else field.flag
            sources[field.pointer] = f"cli:{used}"
        return self.loader.Layer(values=values, sources=sources)

    def resolve(self, ctx: click.Context, config: Any, *, json_flag: bool = False) -> Any:
        """Merge the command-line layer into `config`. On a `ConfigError`, print the envelope and exit 2."""
        try:
            return self.loader.merge(config, self.layer(ctx))
        except self.loader.ConfigError as error:
            self.fail(ctx, error, json_flag=json_flag)

    def fail(self, ctx: click.Context, error: Any, *, json_flag: bool = False) -> None:
        findings = [{"code": CONFIG_INVALID, "severity": "error", **{key: item[key] for key in FINDING_KEYS if key in item}}
                    for item in error.errors]
        _emit().emit({"status": "fail", "findings": findings}, tool=self.tool, name="config", code=EXIT_USAGE,
                     json_flag=json_flag)
        ctx.exit(EXIT_USAGE)
