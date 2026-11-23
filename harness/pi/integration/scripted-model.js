// SPDX-FileCopyrightText: 2026 Sjors Robroek
//
// SPDX-License-Identifier: MPL-2.0

// Test-only pi extension for harness/test_pi_integration.py. It registers pi-ai's faux
// provider as `cadtest/scripted`, whose replies follow a script, so a real pi and
// pi-subagents run with no model service. The parent and the child share the provider
// (a foreground child inherits the parent's providers).
//
// CAD_PI_SCRIPT names a JSON file: {"childMarker": text, "parent": [call...], "child": [call...]},
// each call `{"name": tool, "arguments": {...}}`. A session whose system prompt holds
// childMarker is the child. Reply N makes call N, then a final text. CAD_PI_LOG receives one
// JSON line per tool result the model sees: {who, tool, isError, text}.

import fs from "node:fs";
import { fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";

const SCRIPT = JSON.parse(fs.readFileSync(process.env.CAD_PI_SCRIPT, "utf8"));
const LOG = process.env.CAD_PI_LOG;

function text(message) {
  return (message.content ?? []).map((part) => part.text ?? "").join("");
}

function reply(context) {
  const messages = context.messages ?? [];
  const who = JSON.stringify(messages[0] ?? {}).includes(SCRIPT.childMarker) ? "child" : "parent";
  const results = messages.filter((message) => message.role === "toolResult");
  const last = results.at(-1);
  if (last) fs.appendFileSync(LOG, JSON.stringify({ who, tool: last.toolName, isError: Boolean(last.isError), text: text(last) }) + "\n");
  const calls = SCRIPT[who];
  if (results.length >= calls.length) return fauxAssistantMessage(`${who} done`);
  const call = calls[results.length];
  return fauxAssistantMessage(fauxToolCall(call.name, call.arguments), { stopReason: "toolUse" });
}

export default function (pi) {
  const faux = fauxProvider({ provider: "cadtest", models: [{ id: "scripted" }] });
  faux.setResponses(Array.from({ length: SCRIPT.parent.length + SCRIPT.child.length + 4 }, () => reply));
  pi.registerProvider(faux.provider);
}
