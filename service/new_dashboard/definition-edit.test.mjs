// A defined agent's Edit: one patch of only the changed definition fields, runtimes its host can launch.

import assert from "node:assert/strict";
import { test } from "node:test";

import { definitionFieldset, definitionPatch, definitionRuntimeOptions, readDefinitionForm } from "./definition-edit.mjs";

const harnessOfRuntime = { "claude-code": "claude", codex: "codex", hermes: "hermes" };
const agent = {
  id: "coder", runtime: "claude-code",
  definition: { state: "defined", ownerMachineId: "win32:box", harnessOfRuntime,
    fields: { name: "Coder", role: "coder", harness: "claude", workspace: "C:/w", model: "", effort: "", instructions: "be brief" } },
};

test("ONLY WHAT CHANGED IS SENT, and an untouched form sends nothing", () => {
  const same = { name: "Coder", role: "coder", workspace: "C:/w", model: "", effort: "", instructions: "be brief", runtime: "claude-code" };
  assert.deepEqual(definitionPatch(agent, same), {});
  assert.deepEqual(definitionPatch(agent, { ...same, model: "opus", effort: "high", instructions: "" }),
    { model: "opus", effort: "high", instructions: "" });
});

test("A RUNTIME IS SENT AS THE HARNESS ITS DEFINITION NAMES", () => {
  assert.deepEqual(definitionPatch(agent, { runtime: "codex" }), { harness: "codex" });
  assert.deepEqual(definitionPatch(agent, { runtime: "pi" }), {}, "a runtime no definition can name is never sent");
});

test("ONLY RUNTIMES ITS HOST ADVERTISES and a definition can name are offered, with its current one", () => {
  const environments = [
    { machineId: "win32:box", runtimes: [{ runtime: "codex", available: true }, { runtime: "pi", available: true },
      { runtime: "hermes", available: false }] },
    { machineId: "wsl:other", runtimes: ["hermes"] },
  ];
  assert.deepEqual(definitionRuntimeOptions(agent, environments), ["claude-code", "codex"]);
});

test("THE FORM SHOWS THE DEFINITION'S VALUES, escaped, and reads back what it shows", () => {
  const html = definitionFieldset({ ...agent, definition: { ...agent.definition,
    fields: { ...agent.definition.fields, instructions: "<b>x</b>" } } }, []);
  assert.match(html, /id="edit-def-name" type="text" value="Coder"/);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;<\/textarea>/);
  assert.match(html, /next started; Restart applies it now/);
  const els = { "edit-def-name": { value: " Coder 2 " }, "edit-def-instructions": { value: " keep spaces " } };
  assert.deepEqual(readDefinitionForm((id) => els[id] || null), { name: "Coder 2", instructions: " keep spaces " });
});
