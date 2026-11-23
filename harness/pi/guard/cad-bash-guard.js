// SPDX-FileCopyrightText: 2026 Sjors Robroek
//
// SPDX-License-Identifier: MPL-2.0

// Bash guard for the Benchhand CAD agents in pi. pi-subagents loads it into each
// CAD agent's child session (`subagentOnlyExtensions`). It allows one plain
// `uv run --script <script> [args]` command per bash call and blocks the rest,
// the same shell limit the other harnesses give these agents. It fails closed:
// anything it cannot prove to be one plain command is blocked.

// `uv run --script`, then one script inside the installed skills directory ($CAD_SKILLS, set by the
// user's environment; the agent cannot set it, because env prefixes and chains are blocked). No
// other uv option is allowed: `--with`, `--index`, `--from`, `--python` and the like only change
// what uv installs or runs. Script arguments after the script are the script's own.
const PREFIX = /^uv[ \t]+run[ \t]+--script[ \t]+("?)\$CAD_SKILLS\/([A-Za-z0-9_.\/-]+\.py)\1(?=[ \t]|$)/;
const REASON =
  "CAD agents may run only a single `uv run --script "$CAD_SKILLS/<script>.py" ...` command per shell call, with no other uv option: " +
  "no pipes, redirects, `;`, `&&`, `||`, `&`, subshells, command substitution, " +
  "newlines or environment prefixes.";

/** True when `command` is exactly one plain `uv run --script ...` command. */
export function isAllowed(command) {
  if (typeof command !== "string") return false;
  const text = command.trim();
  const head = PREFIX.exec(text);
  if (head === null) return false;
  if (head[2].split("/").some((part) => part === "" || part === "." || part === "..")) return false;
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
      // Only a plain `$NAME` expansion is allowed, as in "$CAD_SKILLS/...".
      if (!/[A-Za-z_]/.test(text[i + 1] ?? "")) return false;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') quote = ch;
    else if (";&|<>()".includes(ch)) return false;
  }
  return quote === null;
}

export default function (pi) {
  pi.on("tool_call", async (event) => {
    if (event.toolName !== "bash") return undefined;
    const command = event.input?.command;
    return isAllowed(command) ? undefined : { block: true, reason: REASON };
  });
}
