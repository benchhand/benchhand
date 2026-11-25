# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["click==8.5.1", "cadquery-ocp-novtk==8.0.1.1.0", "jsonschema>=4.23", "pyyaml>=6.0", "trimesh==4.*", "numpy==2.5.5", "python-fcl==0.7.0.11", "referencing>=0.28.4"]
# ///
"""Build, check, and diff versions of a hardware design from its part model scripts.

Usage:
    uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-model/scripts/cad_model.py run <hw-dir> --label vN [--kernel auto|cadquery|build123d]
                                     [--attach REF=PARENT_REF ...]
    uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-model/scripts/cad_model.py check vN --hw hw [--out FILE]
       [--pair REF:REF ... | --changed-only [--since-version vM]] [--exact] [--allow-interference REF:REF ...]
       [--expect-solids PN=N ...] [--jobs 1|N|auto] [--cache DIR | --no-cache]
    uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-model/scripts/cad_model.py diff vA vB [--hw HW] [--out FILE]
    uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-model/scripts/cad_model.py latest <hw-dir> --label vN
    uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-model/scripts/cad_model.py store gc <hw-dir> [--dry-run]

A REF is a PN placed once, or `@id` for one occurrence of hw/assembly.yaml.

run   executes every hw/parts/<PN>/model.py in its declared kernel's own uv environment
      (run_cadquery.py, run_build123d.py), then writes hw/versions/vN/:
        parts/<PN>.step   AP242, one product named "<PN> <title>": the definition, in the
                          part's model_frame
        assembly.step     AP242, top product "<product id> vN" with one product per PN and one
                          component per occurrence at the joints' nominal pose (cad-motion pose.py):
                          the in-context scene, existing equipment (make_buy existing) included
        assembly.glb      binary glTF of assembly.step, metres, +Y up, one node per occurrence, under
                          a made, purchased or existing group node by the part's make_buy
        product.step      assembly.step without existing equipment: the delivered product, each
                          component under a made or purchased group product
        product.glb       binary glTF of product.step, with its group nodes
        part-map.json     occurrence -> assembly.glb nodes and meshes, plus the PN aggregate
        metrics.json      per PN: volume, area, bbox, solids, holes, mass from its material profile's
                          density, unknown without one;
                          per occurrence: placement, placed bbox, centre and holes; totals by quantity
                          over the delivered occurrences, naming the existing ones
        manifest.json     per PN: kernel, model frame, model path, source and record sha256, params,
                          kernel volume; occurrences with rest placements; tool versions; pose
                          (values, placements, motions); joints and joint pairs; the record
                          snapshot; the manufacturing entry of each custom-made PN; file hashes
        record/           the records the version was built from: product, requirements, interfaces,
                          assembly, parts, manufacturing profiles and selection, and their source bytes
        manufacturing/<PN>/  every custom-made PN: part.step (AP242, mm), part.stl (additive: binary,
                          mm; fdm: part.3mf), components/<COMP>/ of a part of several solids, and manufacturing.json,
                          in the state model.py build_manufacture defines (else the installed
                          definition), an additive part on the bed in its route's approved orientation
      and hw/versions/latest.json pointing at that version (paths relative to hw/). Labels
      increase: an existing label or one below the newest built version is refused. A part whose
      manufacturing output fails a blocking check fails the run: no version, latest unchanged.
check runs the geometry checks on a built version; diff compares two built versions. A check of a
      version built from a record version 2 snapshot also publishes its cad.readback required-check
      packet to hw/checks/<V>/cad/ (stdout `packet`), with the GLB<->STEP agreement and declared
      dimensions among its outcomes. A check is a
      run recorded in hw/.cad/runs (cad-flow process_lifecycle.py): it refuses to start while
      another check runs or an earlier run left processes behind, and prints how to stop them.
      Its pair workers die with it. --changed-only measures only the occurrence pairs whose
      geometry, relative placement or declarations changed since --since-version (default: the
      newest built version before vN); its report is partial (`scope.complete` false).
latest points hw/versions/latest.json at the newest built version after `run` sealed it but could
      not write the pointer; it verifies the version against its manifest first and rebuilds nothing.
store gc removes from the store (hw/.cad/cache, cad-record store.py) every object no sealed
      manifest references and no build record of a referenced output needs, and every other
      build record, and the former part store hw/.cad/parts and hw/.cad/builds; it reports the
      bytes freed. --dry-run removes nothing.

stdout: one JSON document in the tool-result envelope (cad-record emit.py; --out files hold it too). stderr: diagnostics.
Exit codes: 0 pass, 1 a part model or a manufacturing output failed (run) or a blocking check failed
(check), 2 usage or infrastructure error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import click
from cadmodel import build


def load_record_script(name: str, filename: str) -> Any:
    """A cad-record script loaded by path: every skill sits beside cad-record in an installed tree."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[2] / "cad-record" / "scripts" / filename)
        assert spec is not None and spec.loader is not None
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def load_emit() -> Any:
    """cad-record's output emitter."""
    return load_record_script("cad_record_emit", "emit.py")


emit = load_emit()
record_cli = load_record_script("cad_record_cli", "cli.py")

LIFECYCLE_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "cad-flow"
    / "scripts"
    / "process_lifecycle.py"
)


def load_lifecycle() -> Any:
    """cad-flow's process supervision: run records, stray refusal, private process groups."""
    import importlib.util

    name = "cad_flow_process_lifecycle"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, LIFECYCLE_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class _Converter(click.ParamType[Any, Any]):
    """A value type from a function that raises argparse's ArgumentTypeError (cad-motion's `jobs_argument` does)."""

    def __init__(self, name: str, convert: Callable[[str], Any]) -> None:
        self.name = name
        self._convert = convert

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return self._convert(value)
        except argparse.ArgumentTypeError as error:
            self.fail(str(error), param, ctx)
        except (TypeError, ValueError):
            self.fail(f"invalid {self.name} value: {value!r}", param, ctx)


def _pair(text: str) -> tuple[str, str]:
    from cadmodel.motion import load_pose

    a, sep, b = text.partition(":")
    if not sep or not load_pose().is_ref(a) or not load_pose().is_ref(b) or a == b:
        raise argparse.ArgumentTypeError(
            f"expected REF:REF (a PN or @occurrence each), got {text!r}"
        )
    return a, b


def _expect(text: str) -> tuple[str, int]:
    pn, sep, n = text.partition("=")
    if not sep or not build.is_pn(pn) or not n.isdigit():
        raise argparse.ArgumentTypeError(f"expected PN=COUNT, got {text!r}")
    return pn, int(n)


def _jobs(text: str) -> int:
    from cadmodel.motion import load_pair_measure

    return cast(int, load_pair_measure().jobs_argument(text))


PAIR = _Converter("REF:REF", _pair)
EXPECT = _Converter("PN=N", _expect)
JOBS = _Converter("N|auto", _jobs)
PATH = click.Path(path_type=Path, readable=False)  # a plain path: argparse's Path(text) tested nothing about it
HW_ARGUMENT = click.argument("hw", type=PATH)
HW_OPTION = click.option("--hw", type=PATH, default=Path("hw"))
OUT_OPTION = click.option("--out", type=PATH, help="also write the result JSON here")
JSON_OPTION = click.option("--json", "json_flag", is_flag=True,
                           help="print the full JSON result whatever CAD_OUTPUT says")


def _exclusive(first: tuple[str, object], second: tuple[str, object]) -> None:
    """Refuse two options given together, as argparse's mutually exclusive group did."""
    if first[1] and second[1]:
        raise click.UsageError(f"argument {second[0]}: not allowed with argument {first[0]}")


class _Command(click.Command):
    """A command that reads its words by argparse's rules, so existing command lines keep working."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        return super().parse_args(ctx, record_cli.screen_words(self, ctx, args))


class _Group(click.Group):
    command_class = _Command


@click.group(cls=_Group, help="Build, check, and diff CAD design versions",
             context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    pass


@cli.command(help="build hw/versions/<label>/ from the part model scripts")
@HW_ARGUMENT
@click.option("--label", required=True, help="version label: v1, v2, ...")
@click.option("--kernel", type=click.Choice(("auto", *build.KERNELS)), default="auto",
              help="auto: each part's declared KERNEL; otherwise every part must declare this kernel")
@click.option("--attach", multiple=True, metavar="REF=PARENT_REF",
              help="rigidly attach an occurrence without a joint (a PN or @id; repeatable)")
@JSON_OPTION
def run(**params: Any) -> int:
    return execute("run", params | {"attach": list(params["attach"])})


@cli.command(help="geometry checks on a built version")
@click.argument("label")
@HW_OPTION
@click.option("--pair", multiple=True, type=PAIR, metavar="REF:REF",
              help="check only these occurrence pairs for interference (default: all pairs)")
@click.option("--changed-only", is_flag=True,
              help="measure only the pairs a change since the baseline version can affect (partial report)")
@click.option("--since-version", metavar="vM",
              help="--changed-only baseline: a built version (default: the newest one before the checked version)")
@click.option("--exact", is_flag=True,
              help="measure every pair; by default a far pair passes on its proven distance bounds")
@click.option("--allow-interference", multiple=True, type=PAIR, metavar="REF:REF",
              help="undeclared intentional overlap: reported, not blocking (repeatable)")
@click.option("--expect-solids", multiple=True, type=EXPECT, metavar="PN=N",
              help="expected solid count; overrides the record's declared components and the previous "
              "version as baseline")
@OUT_OPTION
@click.option("--jobs", type=JOBS, default="1", metavar="N|auto",
              help="worker processes measuring pairs (1, the default: this process, on OCCT's thread pool; "
              "auto: the CPU count, at most 8)")
@click.option("--cache", type=PATH, metavar="DIR",
              help="pair result cache shared with cad-motion sweep.py (default: hw/.cad)")
@click.option("--no-cache", is_flag=True, help="measure every pair, reading and writing no cache")
@JSON_OPTION
def check(**params: Any) -> int:
    _exclusive(("--pair", params["pair"]), ("--changed-only", params["changed_only"]))
    _exclusive(("--cache", params["cache"]), ("--no-cache", params["no_cache"]))
    if params["since_version"] and not params["changed_only"]:
        raise click.UsageError("--since-version needs --changed-only")
    return execute("check", params | {"pair": list(params["pair"]) or None,
                                      "allow_interference": list(params["allow_interference"]),
                                      "expect_solids": list(params["expect_solids"])})


@cli.command(help="compare two built versions")
@click.argument("a")
@click.argument("b")
@HW_OPTION
@OUT_OPTION
@JSON_OPTION
def diff(**params: Any) -> int:
    return execute("diff", params)


@cli.command(help="point hw/versions/latest.json at the newest sealed version")
@HW_ARGUMENT
@click.option("--label", required=True, help="the sealed version label the pointer write failed for")
@JSON_OPTION
def latest(**params: Any) -> int:
    return execute("latest", params)


@cli.command(help="maintain the store hw/.cad/cache")
@click.argument("action", type=click.Choice(("gc",)))
@HW_ARGUMENT
@click.option("--dry-run", is_flag=True, help="report what gc would remove; remove nothing")
@JSON_OPTION
def store(**params: Any) -> int:
    return execute("store", params)


def main(argv: list[str] | None = None) -> int:
    try:
        code = cli.main(args=argv, standalone_mode=False)
    except click.Abort as error:  # Click turns a Ctrl-C into Abort. Hand the KeyboardInterrupt on, as argparse did.
        raise error.__cause__ if isinstance(error.__cause__, KeyboardInterrupt) else error
    except click.ClickException as error:
        error.show()
        return error.exit_code
    return int(code or 0)


def execute(command: str, params: dict[str, Any]) -> int:
    args = SimpleNamespace(command=command, json=params.pop("json_flag"), **params)
    from cadmodel.motion import load_pair_measure, load_pose

    pose = load_pose()
    lifecycle = load_lifecycle()
    try:
        with pose.stdout_to_stderr():
            if args.command == "run":
                code, result = build.run(args.hw, args.label, args.kernel, args.attach)
            elif args.command == "check":
                from cadmodel.check import check

                cache_dir = (
                    None
                    if args.no_cache
                    else (
                        args.cache
                        or load_pair_measure().default_cache_dir(args.hw.resolve())
                    )
                )
                build.version_dir(
                    args.hw, args.label
                )  # no run record in a folder that holds no such version
                selection = (
                    f"changed-pairs since {args.since_version or 'previous'}"
                    if args.changed_only
                    else "named-pairs"
                    if args.pair
                    else "all-pairs"
                )
                with lifecycle.claim_run(
                    args.hw, "static", scope=f"{args.label} {selection}"
                ):
                    code, result = check(
                        args.hw,
                        args.label,
                        args.pair,
                        args.allow_interference,
                        dict(args.expect_solids),
                        cache_dir,
                        args.jobs,
                        exact=args.exact,
                        changed_only=args.changed_only,
                        since=args.since_version,
                    )
            elif args.command == "diff":
                from cadmodel.diff import diff

                code, result = diff(args.hw, args.a, args.b)
            elif args.command == "store":
                code, result = build.store_gc(args.hw, args.dry_run)
            else:
                code, result = build.reconcile_latest(args.hw, args.label)
    except (
        build.UsageError,
        pose.MotionError,
        lifecycle.LifecycleError,
        OSError,
    ) as error:
        print(f"cad_model {args.command}: {error}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - an unexpected failure is no result, never a check failure
        traceback.print_exc()
        return 2
    out = getattr(args, "out", None) or None
    hw = getattr(args, "hw", None) or Path.cwd() / "hw"
    emit.emit(result, tool=f"cad_model {args.command}", name=f"cad_model-{args.command}", code=code,
              scratch=emit.work_dir(hw), out=out, facts={"label": result.get("label")}, json_flag=args.json)
    return code


if __name__ == "__main__":
    sys.exit(main())
