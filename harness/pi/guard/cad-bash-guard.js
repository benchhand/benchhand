// SPDX-FileCopyrightText: 2026 Sjors Robroek
//
// SPDX-License-Identifier: MPL-2.0

// Bash guard for the Benchhand CAD agents in pi. pi-subagents loads it into each
// CAD agent's child session (`subagentOnlyExtensions`). It allows one plain
// `uv run --script <script> [args]` command per bash call and blocks the rest,
// the same shell limit the other harnesses give these agents. It fails closed:
// anything it cannot prove to be one plain command is blocked.
//
// The script must be a real `.py` file inside the installed skills directory, which
// sits beside this guard's folder (`<scope>/cad-guard/` and `<scope>/skills/`). uv
// takes no flag: the script follows `--script` at once, so `--with`, `--index-url`,
// `--from` and `--python` never reach it. A URL, `-`, a missing file or a symlink
// that leaves the skills directory is blocked. `$CAD_SKILLS` is accepted only when
// the environment holds the installed skills directory.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SKILLS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "skills");
const PREFIX = /^uv[ \t]+run[ \t]+--script[ \t]+\S/;
const REASON =
  "CAD agents may run only a single plain `uv run --script <installed skill script>` command per shell call: " +
  "no uv flags, no pipes, redirects, `;`, `&&`, `||`, `&`, subshells, command substitution, " +
  "newlines or environment prefixes.";

function realpath(target) {
  try {
    return fs.realpathSync(target);
  } catch {
    return null;
  }
}

/** The words of a command that already passed the shell-shape scan (quotes only, no escapes). */
function words(text) {
  const out = [];
  let current = null;
  let quote = null;
  for (const ch of text) {
    if (quote) {
      if (ch === quote) quote = null;
      else current += ch;
    } else if (ch === "'" || ch === '"') {
      quote = ch;
      current ??= "";
    } else if (ch === " " || ch === "\t") {
      if (current !== null) out.push(current);
      current = null;
    } else {
      current = (current ?? "") + ch;
    }
  }
  if (current !== null) out.push(current);
  return out;
}

/** True when the script word is a `.py` file that resolves inside the installed skills directory. */
function scriptInside(word, { skillsDir, cwd, env }) {
  const root = realpath(skillsDir);
  if (root === null || word === "" || word.startsWith("-") || word.includes("://")) return false;
  let target = word;
  const variable = /^\$(?:CAD_SKILLS|\{CAD_SKILLS\})(?=\/)/.exec(word);
  if (variable) {
    if (realpath(env.CAD_SKILLS ?? "") !== root) return false;
    target = root + word.slice(variable[0].length);
  }
  if (/[$*?[\]{}~]/.test(target) || !target.endsWith(".py")) return false;
  const resolved = realpath(path.resolve(cwd, target));
  return resolved !== null && resolved.startsWith(root + path.sep) && fs.statSync(resolved).isFile();
}

/** True when `command` is exactly one plain `uv run --script <installed script> ...` command. */
export function isAllowed(command, options = {}) {
  if (typeof command !== "string") return false;
  const settings = {
    skillsDir: options.skillsDir ?? SKILLS_DIR,
    cwd: options.cwd ?? process.cwd(),
    env: options.env ?? process.env,
  };
  const text = command.trim();
  if (!PREFIX.test(text)) return false;
  let quote = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "\n" || ch === "\r" || ch === "\0") return false;
    if (quote === "'") {
      if (ch === "'") quote = null;
      continue;
    }
    if (ch === "\\" || ch === "`") return false;
    if (ch === "$") {
      // Only a plain `$NAME` or `${NAME}` expansion is allowed, as in "$CAD_SKILLS/...".
      if (!/[A-Za-z_{]/.test(text[i + 1] ?? "")) return false;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') quote = ch;
    else if (";&|<>()".includes(ch)) return false;
  }
  if (quote !== null) return false;
  return scriptInside(words(text)[3] ?? "", settings);
}

export default function (pi) {
  pi.on("tool_call", async (event) => {
    if (event.toolName !== "bash") return undefined;
    const command = event.input?.command;
    return isAllowed(command) ? undefined : { block: true, reason: REASON };
  });
}
