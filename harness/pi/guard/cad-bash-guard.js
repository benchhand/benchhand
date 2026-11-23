// SPDX-FileCopyrightText: 2026 Sjors Robroek
//
// SPDX-License-Identifier: MPL-2.0

// Bash guard for the Benchhand CAD agents in pi. pi-subagents loads it into each
// CAD agent's child session (`subagentOnlyExtensions`). It allows one plain
// `uv run --script <script> [args]` command per bash call and blocks the rest,
// the same shell limit the other harnesses give these agents. It fails closed:
// anything it cannot prove to be one plain command is blocked.

const PREFIX = /^uv[ \t]+run[ \t]+--script[ \t]+\S/;
const REASON =
  "CAD agents may run only a single plain `uv run --script ...` command per shell call: " +
  "no pipes, redirects, `;`, `&&`, `||`, `&`, subshells, command substitution, " +
  "newlines or environment prefixes.";

/** True when `command` is exactly one plain `uv run --script ...` command. */
export function isAllowed(command) {
  if (typeof command !== "string") return false;
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
