"""Click options from a JSON Schema config, with the config loader's source reporting.

Load it by path, as `emit.py`. It imports Click only when it builds options. A field with `x-cli` becomes an
option, and `x-env` becomes its `envvar`, so Click checks the env var like the flag. The caller passes in the
loader module. `screen_words` gives a converted script argparse's word rules. See its docstring.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable, Mapping, Sequence
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
    def spellings(self) -> list[str]:
        return self.declaration.split("/")

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


def _end(sub: Mapping[str, Any], closed_key: str, open_key: str, pick: Callable[..., Any]) -> tuple[Any, bool]:
    """The bound at one end and whether it is open. With both forms present, the tighter wins and a tie is open."""
    closed, opened = sub.get(closed_key), sub.get(open_key)
    if opened is None:
        return closed, False
    if closed is None:
        return opened, True
    tight = pick(closed, opened)
    return tight, tight == opened


def _bounds(sub: Mapping[str, Any]) -> dict[str, Any]:
    low, low_open = _end(sub, "minimum", "exclusiveMinimum", max)
    high, high_open = _end(sub, "maximum", "exclusiveMaximum", min)
    return {"min": low, "max": high, "min_open": low_open, "max_open": high_open}


def _param_type(field: Field) -> click.ParamType[Any, Any]:
    import click

    sub, kind = field.schema, field.schema.get("type")
    if "enum" in sub:
        return click.Choice(sub["enum"])
    bounds = _bounds(sub)
    ranged = bounds["min"] is not None or bounds["max"] is not None
    if kind == "integer":
        return click.IntRange(**bounds) if ranged else click.INT
    if kind == "number":
        return click.FloatRange(**bounds) if ranged else click.FLOAT
    return click.STRING


def _check(field: Field) -> None:
    """Refuse an unsupported field with a message that names its flag."""
    sub, kind = field.schema, field.schema.get("type")
    for spelling in field.spellings:
        if not spelling.startswith("--") or len(spelling) == 2:
            raise ValueError(f"{spelling}: only long --flags are supported")
    if kind not in VALUE_TYPES | {"boolean"}:
        raise ValueError(f"{field.flag}: unsupported type {kind!r}")
    if "enum" in sub and not all(isinstance(item, str) for item in sub["enum"]):
        raise ValueError(f"{field.flag}: only string enums are supported")
    if any(isinstance(sub.get(key), bool) for key in ("exclusiveMinimum", "exclusiveMaximum")):
        raise ValueError(f"{field.flag}: draft-4 boolean exclusive bounds are not supported; use a number")


def _check_all(found: Sequence[Field]) -> None:
    for field in found:
        _check(field)
    spellings = [spelling for field in found for spelling in field.spellings]
    if len(set(spellings)) != len(spellings):
        raise ValueError(f"duplicate flag: {sorted(s for s in set(spellings) if spellings.count(s) > 1)}")
    names = [field.name for field in found]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate parameter name: {sorted(n for n in set(names) if names.count(n) > 1)}")


def _option(field: Field) -> click.Option:
    import click

    common: dict[str, Any] = {"default": field.schema.get("default"), "envvar": field.schema.get("x-env"),
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


_NUMBER = re.compile(r"^-\d+$|^-\d*\.\d+$")


def option_like(text: str) -> bool:
    """Whether argparse reads `text` as an option and not a value: a dash, no space, and not a negative number.

    A lone `-` is a value. This needs no Click."""
    return text.startswith("-") and text != "-" and " " not in text and not _NUMBER.match(text)


def screen_words(command: click.Command, ctx: click.Context, args: Sequence[str]) -> list[str]:
    """Read `args` by argparse's word rules and return them in the order Click parses them.

    Click takes the next word as an option's value whatever it looks like. It checks only the last of a repeated
    scalar option. It parses a positional such as `-alt hw` as options. To keep existing command lines working,
    a converted script overrides `Command.parse_args` and calls
    `super().parse_args(ctx, screen_words(self, ctx, args))`.

    The screen walks the words in order. It raises Click's `BadOptionUsage` for a value that looks like an
    option and for a value-taking option with no value. It converts every occurrence of a typed option and
    raises `BadParameter` for a bad one. A help flag ends the screen and returns the words so far with
    that flag. After the last word, an unknown option raises `NoSuchOption`, so an earlier bad value
    comes first. The result lists the options with their values, then `--`, then every positional in order.
    Every positional reaches Click after the `--`, so it stays a positional. A `--` in `args` makes every later
    word a positional."""
    import click

    options = {flag: param for param in command.get_params(ctx) if isinstance(param, click.Option)
               for flag in (*param.opts, *param.secondary_opts)}
    helps = set(command.get_help_option_names(ctx))
    screened: list[str] = []
    positionals: list[str] = []
    unknown: str | None = None
    position = 0
    while position < len(args):
        word = args[position]
        position += 1
        if word == "--":
            positionals.extend(args[position:])
            break
        if word in helps:
            return [*screened, word]
        name, equals, value = word.partition("=")
        param = options.get(name) if word.startswith("-") else None
        if param is None and not option_like(word):
            positionals.append(word)
            continue
        if param is None:
            unknown = unknown or name
        elif param.is_flag:
            screened.append(word)
        else:
            if not equals:
                if position >= len(args) or option_like(args[position]):
                    raise click.BadOptionUsage(name, f"Option '{name}' requires an argument.", ctx=ctx)
                value = args[position]
                position += 1
            param.type(value, param, ctx)
            screened.extend([word] if equals else [word, value])
    if unknown is not None:
        raise click.NoSuchOption(unknown, ctx=ctx)
    return [*screened, "--", *positionals] if positionals else screened


def with_warnings(result: Mapping[str, Any], config: Any) -> dict[str, Any]:
    """`result` with the loader's warnings, unchanged, ahead of its own findings."""
    return {**result, "findings": [*config.warnings, *result.get("findings", [])]}


class SchemaCLI:
    """The options of one schema and the bridge to a loader module (`Layer`, `merge`, `ConfigError`)."""

    def __init__(self, schema: Mapping[str, Any], loader: Any, *, tool: str) -> None:
        self.fields = fields(schema)
        self.loader, self.tool = loader, tool
        _check_all(self.fields)

    def needs_click(self, argv: Sequence[str], environ: Mapping[str, str]) -> bool:
        """Whether Click must run: `argv` has a schema flag or `--help`, or `environ` has an `x-env` variable.

        With nothing to check, a caller can skip the Click import and use the loaded config as it is."""
        flags = {"--help"}
        for field in self.fields:
            flags |= set(field.spellings)
        given = any(arg.split("=", 1)[0] in flags for arg in argv)
        names = [field.schema["x-env"] for field in self.fields if "x-env" in field.schema]
        return given or any(name in environ for name in names)

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
