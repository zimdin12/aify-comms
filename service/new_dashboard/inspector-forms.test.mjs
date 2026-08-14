// Real tests for the inspector's form and detail panels.
//
// `buildHandoffPacket` is the one with logic worth pinning: it is the text an operator pastes into another
// agent to hand work over, so a filter that misses one leg of a conversation hands over a half-transcript
// and the receiving agent answers the wrong question. It had no test while it lived in app.js.
//
// SEALING. `state` is a shared singleton, so every field read here is rebuilt per test; `document` does not
// exist in Node and is installed only while rendering.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  buildHandoffPacket,
  openAgentEditForm,
  openMessageDetail,
} from "./inspector-forms.mjs";

function el() {
  const classes = new Set();
  return {
    innerHTML: "",
    value: "",
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
  };
}

function render(run) {
  const els = { "inspector-content": el(), inspector: el() };
  const had = { d: "document" in globalThis, r: "requestAnimationFrame" in globalThis };
  // createElement/body are here because `openMessageDetail` reports a miss through `toast()`, which
  // builds a real node of its own. Without them the failure reads 'document.createElement is not a
  // function', which points at the harness rather than at anything being tested.
  const kids = [];
  globalThis.document = {
    getElementById: (id) => els[id] || null,
    querySelector: () => null,
    createElement: () => ({
      className: "", textContent: "", children: [], firstElementChild: null,
      setAttribute() {}, remove() {}, addEventListener() {},
      classList: { add() {}, remove() {} },
      appendChild: (c) => c,
    }),
    body: { appendChild: (c) => { kids.push(c); return c; } },
  };
  globalThis.requestAnimationFrame = (fn) => fn();
  try {
    run(els);
    return els["inspector-content"].innerHTML;
  } finally {
    if (!had.d) delete globalThis.document;
    if (!had.r) delete globalThis.requestAnimationFrame;
  }
}

test("the handoff packet collects BOTH legs of the conversation", () => {
  // Filtering on `from` alone would hand over only what the agent said and none of what it was asked —
  // the receiving agent then answers a question it cannot see.
  state.messages = [
    { from: "coder", to: "manager", body: "done" },
    { from: "manager", to: "coder", body: "please do it" },
    { from: "tester", to: "manager", body: "unrelated" },
  ];
  const packet = buildHandoffPacket("coder");
  assert.ok(packet.includes("done"));
  assert.ok(packet.includes("please do it"), "inbound messages must be included, not just outbound");
  assert.ok(!packet.includes("unrelated"), "another pair's traffic must not leak into the handoff");
});

test("the `target` recipient spelling counts as a leg", () => {
  // Dispatch-authored rows carry `target` rather than `to`; missing them silently truncates the handoff.
  state.messages = [{ from: "manager", target: "coder", body: "via target" }];
  assert.ok(buildHandoffPacket("coder").includes("via target"));
});

test("the packet keeps the LAST N messages and says how many it has", () => {
  // `.slice(-count)`: a handoff is about recent context, and taking the first N would hand over the
  // beginning of a long conversation instead of where it got to.
  state.messages = Array.from({ length: 40 }, (_, i) => ({ from: "coder", to: "manager", body: `msg ${i}` }));
  const packet = buildHandoffPacket("coder", 5);
  assert.ok(packet.includes("msg 39"), "the newest message must be present");
  assert.ok(!packet.includes("msg 34"), "…and anything older than the window must not");
  assert.ok(packet.includes("last 5 messages"), "the header states how much context is actually included");
});

test("a message with no body falls back to its preview, and an empty conversation still yields a packet", () => {
  state.messages = [{ from: "coder", to: "manager", preview: "just a preview" }];
  assert.ok(buildHandoffPacket("coder").includes("just a preview"));

  state.messages = [];
  const empty = buildHandoffPacket("ghost");
  assert.ok(empty.includes("ghost"), "an empty packet must still name the agent it is about");
  assert.ok(empty.includes("last 0 messages"));
});

test("opening a message that is not loaded warns instead of rendering an empty panel", () => {
  // The detail panel is reachable from a row the poll may have dropped. A blank drawer reads as a bug.
  state.messages = [];
  const html = render(() => openMessageDetail("no-such-id"));
  assert.equal(html, "", "nothing must be rendered for a message that is not there");
});

test("a loaded message renders its from/to and falls back on the target spelling", () => {
  state.messages = [{ id: "m1", from: "manager", target: "coder", type: "task", body: "the body" }];
  const html = render(() => openMessageDetail("m1"));
  assert.ok(html.includes("manager"));
  assert.ok(html.includes("coder"), "the `target` spelling must render as the recipient");
  assert.ok(html.includes("the body"));
});

test("the agent edit form opens for an agent that is no longer in state", () => {
  // Reachable from a drawer the poll has since emptied; throwing here leaves the panel half-written.
  state.agents = [];
  const html = render(() => openAgentEditForm("ghost"));
  assert.ok(html.includes("ghost"));
});

test("the edit form pre-fills from the agent's current record", () => {
  // An edit form that opens blank invites the operator to overwrite fields they meant to leave alone.
  // The form carries description, session handle, environment and runtime — NOT role, which my first
  // version of this test asserted from memory instead of from the markup.
  state.agents = [{ id: "coder", description: "does the work", sessionHandle: "sess-9", runtime: "codex" }];
  state.environments = [];
  const html = render(() => openAgentEditForm("coder"));
  assert.ok(html.includes("does the work"), "the current description must be pre-filled");
  assert.ok(html.includes("sess-9"), "…and the native session handle");
  assert.ok(html.includes('value="codex" selected'), "…and the runtime must come up selected");
});

test("hermes is offered under its canonical backend identifier", () => {
  // Asserted in app.test.mjs as a source regex until v0.5.4. The identifier matters: the dashboard sends
  // this value straight through, so an option labelled anything else would register a runtime the
  // backend does not know.
  state.agents = [{ id: "coder" }];
  state.environments = [];
  const html = render(() => openAgentEditForm("coder"));
  assert.ok(html.includes('value="hermes"'), "hermes must be selectable");
});

test("an unknown runtime is added to the options rather than silently reset", () => {
  // The runtime list is a fixed set plus whatever the agent actually has. Without that union an agent on
  // a runtime the dashboard does not know would open the form showing 'generic' — and saving would
  // change its runtime as a side effect of opening a form.
  state.agents = [{ id: "coder", runtime: "some-future-runtime" }];
  state.environments = [];
  const html = render(() => openAgentEditForm("coder"));
  assert.ok(html.includes('value="some-future-runtime" selected'));
});
