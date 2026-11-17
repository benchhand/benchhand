#!/usr/bin/env python3
"""cad-record cli.py: Click options from a JSON Schema config, and the loader's source reporting.

Run: uv run --no-project --with click==8.5.1 python -B -m unittest discover -s skills/cad-record/tests -p test_cli.py -v
The config loader is a local stand-in (fixtures/config_loader_standin.py) until config_loader.py lands.
"""

from __future__ import annotations

import json
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import click
from click.testing import CliRunner

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from modload import load_fresh, load_module  # noqa: E402

emit = load_module("cad_record_emit", SKILLS / "cad-record" / "scripts" / "emit.py")
CLI_PATH = SKILLS / "cad-record" / "scripts" / "cli.py"
cli_mod = load_module("cad_record_cli", CLI_PATH)
loader = load_module("config_loader_standin", HERE / "fixtures" / "config_loader_standin.py")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "minimum": 1, "maximum": 5, "x-cli": "--count"},
        "rank": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 4, "x-cli": "--rank"},
        "band": {"type": "number", "minimum": 0, "exclusiveMaximum": 10, "default": 2.5,
                 "x-cli": "--mesh-band", "x-env": "CAD_BAND", "description": "Band width."},
        "pitch": {"type": "number", "exclusiveMinimum": 0, "maximum": 1, "x-cli": "--pitch"},
        "kind": {"type": "string", "enum": ["fine", "coarse"], "x-cli": "--kind"},
        "verbose": {"type": "boolean", "x-cli": "--verbose"},
        "label": {"type": "string", "x-cli": "--label"},
        "free": {"type": "number", "x-cli": "--free"},
        "mesh": {"type": "object", "properties": {
            "tol": {"type": "number", "default": 0.1, "x-cli": "--mesh-tol", "x-env": "CAD_MESH_TOL"}}},
        "plain": {"type": "number", "default": 1.0},
    },
}


class Harness:
    """A command over SCHEMA that records its parameters and the CLI layer it built."""

    def __init__(self, ldr: Any = loader) -> None:
        self.cli = cli_mod.SchemaCLI(SCHEMA, ldr, tool="probe")
        self.params: dict[str, Any] = {}
        self.layer: Any = None
        self.resolved: Any = None

    def run(self, args: list[str], *, env: Mapping[str, str] | None = None, config: Any = None):
        @click.pass_context
        def callback(ctx: click.Context, **params: Any) -> None:
            self.params = params
            self.layer = self.cli.layer(ctx)
            self.resolved = self.cli.resolve(ctx, config if config is not None else loader.load(SCHEMA, env=env),
                                             json_flag=True)

        command = click.Command("probe", params=self.cli.params(), callback=callback)
        default_map = self.cli.default_map(config) if config is not None else None
        return CliRunner().invoke(command, args, env=dict(env or {}), default_map=default_map)


class OptionTests(unittest.TestCase):
    def test_each_type_reaches_the_command_as_its_python_type(self) -> None:
        cases = [
            (["--count", "3"], "count", 3),
            (["--mesh-band", "1.5"], "mesh_band", 1.5),
            (["--free", "-7.25"], "free", -7.25),
            (["--kind", "coarse"], "kind", "coarse"),
            (["--verbose", "true"], "verbose", True),
            (["--verbose", "false"], "verbose", False),
            (["--label", "x y"], "label", "x y"),
        ]
        for args, name, expected in cases:
            with self.subTest(args=args):
                harness = Harness()
                result = harness.run(args)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(harness.params[name], expected)
                self.assertIs(type(harness.params[name]), type(expected))

    def test_bounds_closed_and_open(self) -> None:
        cases = [
            (["--count", "1"], True), (["--count", "0"], False),
            (["--count", "5"], True), (["--count", "6"], False),
            (["--rank", "1"], True), (["--rank", "0"], False),
            (["--rank", "3"], True), (["--rank", "4"], False),
            (["--mesh-band", "0"], True), (["--mesh-band", "-0.1"], False),
            (["--mesh-band", "9.9"], True), (["--mesh-band", "10"], False),
            (["--pitch", "0.1"], True), (["--pitch", "0"], False),
            (["--pitch", "1"], True), (["--pitch", "1.1"], False),
        ]
        for args, accepted in cases:
            with self.subTest(args=args):
                result = Harness().run(args)
                self.assertEqual(result.exit_code, 0 if accepted else 2, result.output)

    def test_an_out_of_range_flag_is_a_plain_text_usage_error(self) -> None:
        result = Harness().run(["--count", "9"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("Invalid value for '--count'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_enum_accepts_members_and_rejects_others(self) -> None:
        self.assertEqual(Harness().run(["--kind", "fine"]).exit_code, 0)
        result = Harness().run(["--kind", "medium"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("Invalid value for '--kind'", result.stderr)

    def test_the_schema_default_applies_when_nothing_is_given(self) -> None:
        harness = Harness()
        self.assertEqual(harness.run([]).exit_code, 0)
        self.assertEqual(harness.params["mesh_band"], 2.5)
        self.assertEqual(harness.params["mesh_tol"], 0.1)
        self.assertIsNone(harness.params["count"])

    def test_the_flag_comes_from_x_cli_and_a_field_without_it_has_no_option(self) -> None:
        names = {opt for param in Harness().cli.params() for opt in param.opts}
        self.assertIn("--mesh-band", names)
        self.assertNotIn("--band", names)
        self.assertFalse(any("plain" in name for name in names))

    def test_help_shows_the_schema_description(self) -> None:
        result = Harness().run(["--help"])
        self.assertIn("Band width.", result.stdout)

    def test_the_env_var_comes_from_x_env_with_the_same_range_check(self) -> None:
        harness = Harness()
        self.assertEqual(harness.run([], env={"CAD_BAND": "3"}).exit_code, 0)
        self.assertEqual(harness.params["mesh_band"], 3.0)
        bad = Harness().run([], env={"CAD_BAND": "10"})
        self.assertEqual(bad.exit_code, 2)
        self.assertIn("Invalid value for '--mesh-band'", bad.stderr)

    def test_an_unsupported_type_is_refused_when_the_options_are_built(self) -> None:
        schema = {"properties": {"xs": {"type": "array", "x-cli": "--xs"}}}
        with self.assertRaisesRegex(ValueError, "--xs"):
            cli_mod.SchemaCLI(schema, loader, tool="probe").params()


class FieldTests(unittest.TestCase):
    def test_nested_properties_map_to_json_pointers_with_rfc_6901_escapes(self) -> None:
        schema = {"properties": {
            "a": {"properties": {"b": {"type": "number", "x-cli": "--a-b"}}},
            "x/y": {"type": "number", "x-cli": "--xy"},
            "m~n": {"type": "number", "x-cli": "--mn"},
        }}
        pointers = {f.name: f.pointer for f in cli_mod.fields(schema)}
        self.assertEqual(pointers, {"a_b": "/a/b", "xy": "/x~1y", "mn": "/m~0n"})

    def test_the_module_imports_and_lists_fields_without_click(self) -> None:
        with mock.patch.dict(sys.modules, {"click": None}):
            module = load_fresh("cad_record_cli_noclick", CLI_PATH)
            self.assertEqual([f.name for f in module.fields(SCHEMA)][:2], ["count", "rank"])


class SourceTests(unittest.TestCase):
    def test_the_cli_layer_holds_only_command_line_values_with_their_flag(self) -> None:
        harness = Harness()
        harness.run(["--count", "2", "--mesh-tol", "0.2"], env={"CAD_BAND": "3"})
        self.assertEqual(harness.layer.values, {"count": 2, "mesh": {"tol": 0.2}})
        self.assertEqual(harness.layer.sources, {"/count": "cli:--count", "/mesh/tol": "cli:--mesh-tol"})

    def test_cli_env_and_default_each_report_their_source_after_resolve(self) -> None:
        harness = Harness()
        result = harness.run(["--count", "2"], env={"CAD_BAND": "3"})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(harness.resolved.sources["/count"], "cli:--count")
        self.assertEqual(harness.resolved.sources["/band"], "env:CAD_BAND")
        self.assertEqual(harness.resolved.sources["/mesh/tol"], "default")
        self.assertEqual(harness.resolved.values["band"], 3.0)

    def test_the_command_line_beats_the_env_var(self) -> None:
        harness = Harness()
        harness.run(["--mesh-band", "4"], env={"CAD_BAND": "3"})
        self.assertEqual(harness.resolved.sources["/band"], "cli:--mesh-band")
        self.assertEqual(harness.resolved.values["band"], 4.0)

    def test_default_map_comes_from_the_loaded_values_and_is_not_a_cli_source(self) -> None:
        config = loader.Config({"band": 7.0, "mesh": {"tol": 0.5}}, {"/band": "file:cfg.yaml", "/mesh/tol": "file:cfg.yaml"})
        harness = Harness()
        result = harness.run([], config=config)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual((harness.params["mesh_band"], harness.params["mesh_tol"]), (7.0, 0.5))
        self.assertEqual(harness.layer.sources, {})
        self.assertEqual(harness.resolved.sources["/band"], "file:cfg.yaml")


class ReportTests(unittest.TestCase):
    ERROR = {"message": "band must be below 10", "pointer": "/band", "value": 11, "source": "file:cfg.yaml"}

    def test_a_config_error_exits_2_with_config_invalid_findings_in_the_envelope(self) -> None:
        def merge(config: Any, layer: Any) -> Any:
            raise loader.ConfigError([self.ERROR, {**self.ERROR, "pointer": "/count", "value": 0}])

        failing = SimpleNamespace(**{**vars(loader), "merge": merge})
        result = Harness(failing).run([])
        self.assertEqual(result.exit_code, 2)
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"schema", "status", "findings", "payload"})
        self.assertEqual(document["status"], "fail")
        self.assertEqual(
            document["findings"][0],
            {"code": "config.invalid", "severity": "error", **self.ERROR},
        )
        self.assertEqual([f["pointer"] for f in document["findings"]], ["/band", "/count"])

    def test_loader_warnings_enter_the_envelope_findings_unchanged(self) -> None:
        warning = {"code": "plausibility.band", "severity": "warning", "message": "band is large",
                   "pointer": "/band", "value": 9.9, "source": "cli:--mesh-band"}
        config = loader.Config({"band": 9.9}, {"/band": "user"}, (warning,))
        result = cli_mod.with_warnings({"status": "pass", "findings": [{"code": "x"}]}, config)
        document = emit.envelope(result)
        self.assertEqual(document["findings"], [warning, {"code": "x"}])
        self.assertIs(document["findings"][0], warning)


if __name__ == "__main__":
    unittest.main()
