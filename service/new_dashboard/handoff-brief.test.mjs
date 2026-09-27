// The handoff brief in the Compact / Continue-as form, called for real against a stubbed fetch.
//
// What can go wrong here is what the operator sees: a brief for the wrong session or count, a fill that
// overwrites a packet they were editing, or a slow brief landing in a form they have since left.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { fillHandoffBrief, handoffBriefPath, loadHandoffBrief } from "./handoff-brief.mjs";
import { state } from "./state.mjs";

function field(value = "") {
  return { value, innerHTML: "", classList: { add() {}, remove() {} } };
}

/** Install a document holding the form's two fields and a fetch answering `reply`; returns the probe. */
function harness({ reply = { ok: true, recentMessages: 10, text: "BRIEF" }, status = 200, packet = "", recent = "" } = {}) {
  const els = { "cont-packet": field(packet), "cont-recent": field(recent) };
  const asked = [];
  const toasts = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, raf: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = () => 0;
  globalThis.document = {
    getElementById: (id) => els[id] || null,
    querySelector: () => null,
    createElement: () => {
      const node = { className: "", textContent: "", children: [], setAttribute() {}, remove() {}, addEventListener() {},
        classList: { add() {}, remove() {} }, appendChild: (c) => c };
      toasts.push(node);
      return node;
    },
    body: { appendChild: (c) => c },
  };
  globalThis.fetch = async (url) => {
    asked.push(String(url));
    return { ok: status < 400, status, statusText: "x", text: async () => JSON.stringify(reply) };
  };
  setApiBase("");
  state.sessions = [{ id: "s1", agentId: "coder" }];
  state.inspector = { kind: "continue", sessionId: "s1" };
  return {
    els, asked,
    toastText: () => toasts.map((t) => t.textContent).filter(Boolean).join(" | "),
    restore: () => { globalThis.document = saved.document; globalThis.fetch = saved.fetch; globalThis.requestAnimationFrame = saved.raf; },
  };
}

test("the path names the session and leaves a blank count to the service's default", () => {
  assert.equal(handoffBriefPath("coder", "s1", ""), "/agents/coder/compact/handoff-brief?sessionId=s1");
  assert.equal(handoffBriefPath("coder", "s1", "3"), "/agents/coder/compact/handoff-brief?sessionId=s1&recentMessages=3");
  assert.equal(handoffBriefPath("a b", "", undefined), "/agents/a%20b/compact/handoff-brief");
});

test("loadHandoffBrief returns the service's brief and touches no form", async () => {
  // The submit path calls it directly when the packet box is empty, so it must not depend on the form.
  const h = harness({ packet: "untouched" });
  try {
    const brief = await loadHandoffBrief("coder", "s1", "2");
    assert.equal(brief.text, "BRIEF");
    assert.deepEqual(h.asked, ["/agents/coder/compact/handoff-brief?sessionId=s1&recentMessages=2"]);
    assert.equal(h.els["cont-packet"].value, "untouched");
  } finally { h.restore(); }
});

test("an empty packet is filled, and the count the service applied is shown", async () => {
  const h = harness();
  try {
    const brief = await fillHandoffBrief("s1");
    assert.equal(brief.text, "BRIEF");
    assert.deepEqual(h.asked, ["/agents/coder/compact/handoff-brief?sessionId=s1"]);
    assert.equal(h.els["cont-packet"].value, "BRIEF");
    assert.equal(h.els["cont-recent"].value, "10");
  } finally { h.restore(); }
});

test("the operator's count is what is asked for", async () => {
  const h = harness({ recent: "4", reply: { ok: true, recentMessages: 4, text: "B4" } });
  try {
    await fillHandoffBrief("s1");
    assert.deepEqual(h.asked, ["/agents/coder/compact/handoff-brief?sessionId=s1&recentMessages=4"]);
  } finally { h.restore(); }
});

test("a packet the operator edited is kept, unless they ask to rebuild it", async () => {
  const h = harness({ packet: "my own words" });
  try {
    await fillHandoffBrief("s1");
    assert.equal(h.els["cont-packet"].value, "my own words", "the automatic fill must not overwrite typed text");
    await fillHandoffBrief("s1", { force: true });
    assert.equal(h.els["cont-packet"].value, "BRIEF", "Rebuild replaces it");
  } finally { h.restore(); }
});

test("a brief that lands after the operator left the form is not written into another one", async () => {
  for (const inspector of [{ kind: "agent", agentId: "coder" }, { kind: "continue", sessionId: "s2" }]) {
    const h = harness();
    try {
      const pending = fillHandoffBrief("s1");
      state.inspector = inspector;
      await pending;
      assert.equal(h.els["cont-packet"].value, "", `written into ${JSON.stringify(inspector)}`);
    } finally { h.restore(); }
  }
});

test("a failed brief says so and writes nothing", async () => {
  const h = harness({ status: 404, reply: { detail: "Agent 'coder' not found" } });
  try {
    assert.equal(await fillHandoffBrief("s1"), null);
    assert.equal(h.els["cont-packet"].value, "");
    assert.match(h.toastText(), /Could not load the handoff brief: Agent 'coder' not found/);
  } finally { h.restore(); }
});
