// 0.9.1 3B: the drawer's Model row, set beside what the session actually runs, read from the agent's host when the
// drawer opens. The render is pure; the loader is driven with a fake api, including one that fails.

import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_MODEL_ID, loadAgentModel, renderAgentModel } from "./agent-model.mjs";
import { state } from "./state.mjs";

const set = (model, effort = "") => ({ model: { value: model, from: "agent" }, effort: { value: effort, from: "agent" } });

test("SET AND RUNNING ARE BOTH SHOWN, and an empty setting says it is the harness default", () => {
  const html = renderAgentModel(set("", "high"), { model: "claude-opus-5-5", at: "2026-10-12T10:00:00Z", readAt: "r" });
  assert.match(html, /set: <span class="subtle">harness default<\/span> · high/);
  assert.match(html, /runs: <code>claude-opus-5-5<\/code>/);
  assert.match(renderAgentModel(set("sonnet"), null), /set: sonnet · <span class="subtle">runs: asking its host…/);
});

test("AN UNKNOWN SAYS WHY, and an old answer from a silent host says it is old", () => {
  assert.match(renderAgentModel(set(""), { unknown: "no session handle" }), /runs: unknown, no session handle/);
  const stale = renderAgentModel(set(""), { model: "gpt-6.1-sol", at: "", readAt: "r",
    stale: "its host did not answer within 3 s; this is its answer from r" });
  assert.match(stale, /<code>gpt-6.1-sol<\/code> <span class="subtle">⚠ its host did not answer within 3 s/);
  assert.doesNotMatch(renderAgentModel(set(""), { model: "m", at: "t" }), /⚠/, "a fresh answer was marked stale");
});

function panel() {
  return { innerHTML: "", firstElementChild: null };
}

test("THE LOADER ASKS THIS AGENT'S MODEL, and paints only while the drawer is still on that agent", async () => {
  const host = panel();
  const asked = [];
  state.inspector = { ...state.inspector, kind: "agent", agentId: "a b" };
  await loadAgentModel("a b", { byId: (id) => (id === AGENT_MODEL_ID ? host : null), runsWith: set(""),
    api: async (path) => { asked.push(path); return { actual: { model: "claude-opus-5-5", at: "" } }; } });
  assert.deepEqual(asked, ["/agents/a%20b/model"]);
  assert.match(host.innerHTML, /claude-opus-5-5/);

  const other = panel();
  state.inspector = { ...state.inspector, agentId: "someone-else" };
  await loadAgentModel("a b", { byId: () => other, runsWith: set(""), api: async () => ({ actual: { model: "late" } }) });
  assert.equal(other.innerHTML, "", "a late answer painted into another agent's drawer");
});

test("A FAILED ASK IS SAID, not shown as unknown", async () => {
  const host = panel();
  state.inspector = { ...state.inspector, kind: "agent", agentId: "a" };
  await loadAgentModel("a", { byId: () => host, runsWith: set(""), api: async () => { throw new Error("503"); } });
  assert.match(host.innerHTML, /runs: could not ask \(503\)/);
});

test("A REPAINTED DRAWER KEEPS THE LAST ANSWER while it asks again, rather than blinking back to asking", async () => {
  const { modelRowBeforeAnswer } = await import("./agent-model.mjs");
  assert.match(modelRowBeforeAnswer("never-opened", set("")), /asking its host/);
  state.inspector = { ...state.inspector, kind: "agent", agentId: "kept" };
  await loadAgentModel("kept", { byId: () => panel(), runsWith: set(""), api: async () => ({ actual: { model: "claude-opus-5-5" } }) });
  assert.match(modelRowBeforeAnswer("kept", set("")), /claude-opus-5-5/);
  assert.doesNotMatch(modelRowBeforeAnswer("kept", set("")), /asking/);
  assert.match(modelRowBeforeAnswer("kept", set("sonnet")), /set: sonnet/, "a changed setting was hidden behind the kept row");
});
