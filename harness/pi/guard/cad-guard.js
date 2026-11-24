// SPDX-FileCopyrightText: 2026 Sjors Robroek
//
// SPDX-License-Identifier: MPL-2.0

// The pi extension behind the Benchhand CAD agents. Each agent loads a generated file
// beside this one (`<scope>/cad-guard/<agent>.js`, named in its `subagentOnlyExtensions`)
// that calls `cadGuard` with the launchers its source names.
//
// The agents get no shell. `cad_run` runs one documented skill script:
//   uv run --script <skills>/cad-flow/scripts/run.py <skills>/<launcher> <args...>
// with no shell, from the session's project root, with a fixed environment. The launcher
// must be one of the agent's names; both paths are checked again after realpath and
// must be the files the installer put in the skills directory beside this folder.
//
// A `tool_call` hook blocks `bash` and `powershell` outright, and blocks `write` or `edit`
// unless the real path lies inside the project's `.cad-work/` and outside the installed
// skills and this folder. It fails closed: a path it cannot resolve is blocked.

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const GUARD_DIR = path.dirname(fileURLToPath(import.meta.url));
export const RUN_PY = "cad-flow/scripts/run.py";
const WORK_DIR = ".cad-work";
const SHELL_TOOLS = new Set(["bash", "powershell"]);
const WRITE_TOOLS = new Set(["write", "edit"]);
const OUTPUT_TAIL = 32 * 1024;
// Environment names the child keeps from the parent. The model sets none of them.
const ENV_KEEP = [
  "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
  "XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
  "FREECADCMD", "CCX", "GMSH", "CAD_REQUIRE_FREECAD", "CAD_FEA_METALS_NEAR_SUPPORT", "CAD_CALIBRATION", "CAD_OUTPUT",
];

function realpath(target) {
  try {
    return fs.realpathSync(target);
  } catch {
    return null;
  }
}

function inside(child, parent) {
  return child === parent || child.startsWith(parent + path.sep);
}

/** The installed skills directory, `<scope>/skills` beside `<scope>/cad-guard/`. */
export function skillsDir(guardDir = GUARD_DIR) {
  return path.resolve(guardDir, "..", "skills");
}

/** The installed file `relative` names, by realpath, or null when it is not exactly that file. */
function installedFile(relative, root) {
  if (typeof relative !== "string" || !/^cad-[a-z0-9-]+\/scripts\/[A-Za-z0-9_]+\.py$/.test(relative)) return null;
  const expected = path.join(root, ...relative.split("/"));
  const real = realpath(expected);
  if (real !== expected || !fs.statSync(real).isFile()) return null;
  return real;
}

/** The run.py and target paths for `launcher`, or an Error naming why it is refused. */
export function resolveLauncher(launcher, launchers, skills = skillsDir()) {
  if (!launchers.includes(launcher)) return new Error(`cad_run refuses launcher ${JSON.stringify(launcher)}: this agent may run only ${launchers.join(", ")}`);
  const root = realpath(skills);
  if (root === null) return new Error(`cad_run cannot find the installed skills directory ${skills}`);
  const runPy = installedFile(RUN_PY, root);
  const target = installedFile(launcher, root);
  if (runPy === null || target === null) return new Error(`cad_run refuses ${launcher}: it or ${RUN_PY} is not a plain installed file under ${root}`);
  return { runPy, target };
}

/** The uv argument vector for one cad_run call. */
export function command(resolved, args) {
  return ["run", "--script", resolved.runPy, resolved.target, ...args];
}

/** The fixed child environment: the kept names from `env`, plus CAD_SKILLS. */
export function childEnv(env, skills) {
  const out = {};
  for (const name of ENV_KEEP) if (typeof env[name] === "string") out[name] = env[name];
  out.CAD_SKILLS = skills;
  return out;
}

/** True when `target` names nothing on disk; false when it exists, as a dangling link does, or cannot be checked. */
function missing(target) {
  try {
    fs.lstatSync(target);
    return false;
  } catch (error) {
    return error?.code === "ENOENT";
  }
}

/**
 * The real path `raw` would write, with missing tail parts kept, or null when it cannot be resolved.
 * Only parts that do not exist are kept as text: a link that does not resolve (dangling, or a loop)
 * makes the path unresolvable, because the write would follow it to a place no check has seen.
 */
function realTarget(raw, cwd) {
  const text = raw.startsWith("@") ? raw.slice(1) : raw;
  if (text === "" || text.startsWith("~") || text.includes("\0") || text.split(/[\\/]/).includes("..")) return null;
  let current = path.resolve(cwd, text);
  const tail = [];
  for (;;) {
    const real = realpath(current);
    if (real !== null) return path.join(real, ...tail);
    if (!missing(current)) return null;
    const parent = path.dirname(current);
    if (parent === current) return null;
    tail.unshift(path.basename(current));
    current = parent;
  }
}

/** Null when a write or edit of `raw` is allowed, else the reason it is blocked. */
export function writeProblem(raw, { cwd, skills = skillsDir(), guardDir = GUARD_DIR }) {
  if (typeof raw !== "string") return "the path is not a string";
  const target = realTarget(raw, cwd);
  const project = realpath(cwd);
  if (target === null || project === null) return `the path ${JSON.stringify(raw)} cannot be resolved without "..", "~", a missing root or a link that does not resolve`;
  for (const protectedDir of [skills, guardDir]) {
    const real = realpath(protectedDir) ?? path.resolve(protectedDir);
    if (inside(target, real)) return `${target} is inside the installed ${real}`;
  }
  // The write creates or replaces the last part inside its parent, so the parent's real path must lie in the work dir.
  const work = path.join(project, WORK_DIR);
  if (!inside(path.dirname(target), work)) return `${target} is outside ${work}`;
  return null;
}

/** The `tool_call` decision: undefined allows the call, `{block, reason}` blocks it. */
export function toolCallDecision(event, { cwd, skills = skillsDir(), guardDir = GUARD_DIR }) {
  if (SHELL_TOOLS.has(event.toolName)) return { block: true, reason: "CAD agents have no shell; run documented skill scripts with cad_run." };
  if (WRITE_TOOLS.has(event.toolName)) {
    const problem = writeProblem(event.input?.path, { cwd, skills, guardDir });
    if (problem) return { block: true, reason: `CAD agents write only inside the project's ${WORK_DIR}/: ${problem}.` };
  }
  return undefined;
}

function tail(chunks) {
  const text = Buffer.concat(chunks).toString("utf8");
  return text.length > OUTPUT_TAIL ? `[... ${text.length - OUTPUT_TAIL} characters cut]\n` + text.slice(-OUTPUT_TAIL) : text;
}

function run(argv, { cwd, env, signal }) {
  return new Promise((resolve) => {
    const out = [];
    const err = [];
    const child = spawn("uv", argv, { cwd, env, shell: false, stdio: ["ignore", "pipe", "pipe"] });
    const abort = () => child.kill("SIGTERM");
    signal?.addEventListener("abort", abort, { once: true });
    child.stdout.on("data", (data) => out.push(data));
    child.stderr.on("data", (data) => err.push(data));
    child.on("error", (error) => resolve({ code: null, error: String(error), stdout: tail(out), stderr: tail(err) }));
    child.on("close", (code, killed) => {
      signal?.removeEventListener("abort", abort);
      resolve({ code, signal: killed, stdout: tail(out), stderr: tail(err) });
    });
  });
}

/** The pi extension factory for one CAD agent and its launchers. */
export function cadGuard(agent, launchers, { guardDir = GUARD_DIR } = {}) {
  const allowed = Object.freeze([...launchers]);
  const skills = skillsDir(guardDir);
  return function extension(pi) {
    pi.on("tool_call", async (event, ctx) => toolCallDecision(event, { cwd: ctx.cwd, skills, guardDir }));
    pi.registerTool({
      name: "cad_run",
      label: "CAD run",
      description:
        `Run one documented CAD skill script for ${agent}, as \`uv run --script "$CAD_SKILLS/${RUN_PY}" <launcher> <args...>\`, ` +
        "from the project root, without a shell. Returns the exit code, stdout and stderr.",
      promptSnippet: `Run a documented CAD skill script (${allowed.join(", ")})`,
      promptGuidelines: [
        `For each documented \`uv run --script "$CAD_SKILLS/${RUN_PY}" <launcher> <args...>\` command, call cad_run with launcher set to <launcher> and args set to the remaining words, one array item per word, without shell quoting.`,
        "cad_run is the only way to run a command: there is no shell, so pipes, redirects, chains and background jobs do not exist.",
      ],
      parameters: {
        type: "object",
        properties: {
          launcher: { type: "string", enum: [...allowed], description: "The skill script, relative to the skills directory" },
          args: { type: "array", items: { type: "string" }, description: "The script's arguments, one word per item" },
        },
        required: ["launcher", "args"],
        additionalProperties: false,
      },
      executionMode: "sequential",
      async execute(_toolCallId, params, signal, _onUpdate, ctx) {
        const args = params?.args;
        if (!Array.isArray(args) || !args.every((word) => typeof word === "string" && !word.includes("\0"))) {
          throw new Error("cad_run args must be a list of strings");
        }
        const resolved = resolveLauncher(params?.launcher, allowed, skills);
        if (resolved instanceof Error) throw resolved;
        const result = await run(command(resolved, args), { cwd: ctx.cwd, env: childEnv(process.env, realpath(skills)), signal });
        if (result.code === null) {
          const why = result.error ?? `killed by ${result.signal}`;
          return { content: [{ type: "text", text: `cad_run ${params.launcher}: no exit code (${why})\n${result.stderr}` }], details: { launcher: params.launcher, exitCode: null }, isError: true };
        }
        const text = `exit ${result.code}\n--- stdout ---\n${result.stdout}\n--- stderr ---\n${result.stderr}`;
        return { content: [{ type: "text", text }], details: { launcher: params.launcher, exitCode: result.code } };
      },
    });
  };
}
