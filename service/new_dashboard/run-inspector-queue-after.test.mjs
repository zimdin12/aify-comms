// The run inspector's Queue-after branch, driven through its real caller with a fake dialog and a fake
// transport (review R5 of 851d0623). One focused test, not an inspector framework: what it pins is that a
// send whose outcome is unknown reopens the prompt WITH the operator's text and says it may have landed.
import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import { handleRunInspectorControl, initRunInspector } from "./run-inspector.mjs";

const noop = () => {};
const node = () => ({
  className: "", textContent: "", innerHTML: "", style: {}, dataset: {}, children: [], hidden: false,
  setAttribute: noop, removeAttribute: noop, appendChild: noop, remove: noop, focus: noop,
  addEventListener: noop, removeEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
  classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
});

// A prompt the test answers: each dialog records its message and pre-filled text, then takes the next
// answer (null = Cancel).
function fakeDialogs(answers) {
  const seen = [];
  const overlay = () => {
    const input = { value: "", focus: noop };
    const handlers = {};
    const button = (name) => ({ ...node(), addEventListener: (_e, fn) => { handlers[name] = fn; } });
    const confirm = button("confirm");
    const cancel = button("cancel");
    const el = { ...node(), querySelector: (sel) => ({ ".dialog-input": input, ".dialog-confirm": confirm, ".dialog-cancel": cancel }[sel] || null) };
    el.answer = () => {
      seen.push({ message: el.innerHTML, prefilled: input.value });
      const next = answers.shift();
      if (next === null) handlers.cancel(); else { input.value = next; handlers.confirm(); }
    };
    return el;
  };
  globalThis.document = {
    ...node(), activeElement: null, body: { ...node(), appendChild: (el) => { if (el.className === "dialog-overlay") setTimeout(el.answer, 0); } },
    createElement: (tag) => (tag === "div" ? overlay() : node()), getElementById: () => null,
  };
  return seen;
}

test("an unknown-outcome Queue-after reopens the prompt with the text and says it may have been sent", async () => {
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  const previousRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const sends = [];
  globalThis.fetch = async (url, opts = {}) => {
    if (String(url).endsWith("/messages/send")) {
      sends.push(JSON.parse(opts.body).body);
      if (sends.length === 1) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify({ ok: true, messageId: "m1", dispatchRuns: [{ status: "queued" }] }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  };
  const dialogs = fakeDialogs(["a long follow-up", "a long follow-up"]);
  try {
    setApiBase("http://test.invalid/api/v1");
    initRunInspector({ closeInspector: noop, evaluateFlowGates: noop, openInspector: noop, openRunConsole: noop, refresh: async () => {}, renderDiagnosticsBulkToolbar: noop });
    state.inspector = { kind: "run", runId: "run-7", run: { id: "run-7", agentId: "coder", status: "failed", messageId: "m1" }, events: [] };
    await handleRunInspectorControl("queue-after");
    assert.equal(dialogs.length, 2, "the prompt reopened once after the lost answer");
    assert.equal(dialogs[1].prefilled, "a long follow-up", "the reopened prompt carries the operator's text");
    assert.match(dialogs[1].message, /may have been sent/, "a lost answer is not called a refusal");
    assert.deepEqual(sends, ["a long follow-up", "a long follow-up"]);
  } finally {
    globalThis.document = previousDocument;
    globalThis.fetch = previousFetch;
    globalThis.requestAnimationFrame = previousRaf;
  }
});
