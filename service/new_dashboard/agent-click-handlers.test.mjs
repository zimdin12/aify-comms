// The agent lifecycle click handlers, tested by CALLING them.
//
// `startColdAgent` is the reason this file is worth its length. It disables its own button and rewrites
// the label to "Starting…" BEFORE an async POST, so every path out has to leave the control usable. The
// success path deliberately does NOT restore it — the button is expected to disappear on the refresh —
// while the failure path must put both back. Getting that backwards leaves a permanently dead button
// after any transient error, and nothing in a review would show it.

import assert from "node:assert/strict";
import test from "node:test";

import { startColdAgent, switchModeFromChip } from "./agent-click-handlers.mjs";

/** A button double recording what the handler did to it. */
function button(id = "coder-1") {
  return { disabled: false, textContent: "Start agent", dataset: { agentId: id } };
}

// `api` cannot be monkey-patched through an ESM namespace (the bindings are read-only), so the network
// boundary is stubbed where it actually is: global fetch, which `api` calls.
function withFetch({ ok = true, body = {}, reject = false } = {}, run) {
  const had = "fetch" in globalThis;
  const prev = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init?.method, body: init?.body });
    if (reject) throw new Error("network down");
    return {
      ok,
      status: ok ? 200 : 500,
      headers: { get: () => "application/json" },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  };
  const hadDoc = "document" in globalThis;
  const prevDoc = globalThis.document;
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({
      className: "", textContent: "", style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {},
      querySelectorAll: () => [], firstChild: null, children: [],
    }),
    body: { appendChild() {}, contains: () => true },
  };
  const hadRaf = "requestAnimationFrame" in globalThis;
  globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
  return Promise.resolve(run(calls)).finally(() => {
    if (had) globalThis.fetch = prev; else delete globalThis.fetch;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
    if (!hadRaf) delete globalThis.requestAnimationFrame;
  });
}

test("startColdAgent disables the button and shows progress BEFORE the request goes out", () => {
  // Synchronous, and asserted before any await: a double-click while the POST is in flight would spawn
  // the agent twice, and the disable is the only thing preventing it.
  return withFetch({}, async () => {
    const btn = button();
    startColdAgent(btn, () => {});
    assert.equal(btn.disabled, true, "disabled immediately, not in the callback");
    assert.equal(btn.textContent, "Starting…");
    // Let the in-flight request settle BEFORE the stubs are torn down. Without this the POST resolves
    // against a restored global and its rejection lands with no handler, failing the file rather than
    // any test — which is exactly how it first failed here.
    await new Promise((r) => setTimeout(r, 0));
  });
});

test("it POSTs the start control to the agent named in the dataset", () => {
  return withFetch({}, async (calls) => {
    const btn = button("tester-2");
    startColdAgent(btn, () => {});
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/agents\/tester-2\/control$/, "the id is URL-encoded into the path");
    assert.equal(calls[0].method, "POST");
    assert.deepEqual(JSON.parse(calls[0].body), { action: "start", from_agent: "dashboard" });
  });
});

test("an agent id with URL-significant characters is ENCODED, not interpolated raw", () => {
  // `encodeURIComponent`. A slash in an id would otherwise address a different route entirely.
  return withFetch({}, async (calls) => {
    startColdAgent(button("team/coder 1"), () => {});
    await new Promise((r) => setTimeout(r, 0));
    assert.match(calls[0].url, /\/agents\/team%2Fcoder(%20|\+)1\/control$/);
  });
});

test("SUCCESS refreshes, and deliberately leaves the button disabled", () => {
  // The control is expected to vanish on the refresh that follows. Re-enabling it here would flash a
  // live "Start agent" button for an agent that is already starting.
  return withFetch({ body: {} }, async () => {
    const btn = button();
    let refreshed = 0;
    startColdAgent(btn, () => { refreshed += 1; });
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(refreshed, 1, "the fleet must be refetched so the new worker appears");
    assert.equal(btn.disabled, true);
  });
});

test("FAILURE RESTORES BOTH the disabled flag and the label", () => {
  // The path that matters. Restoring one and not the other leaves either a dead button or one that lies
  // about what it will do; restoring neither leaves the operator with no way to retry short of a reload.
  return withFetch({ reject: true }, async () => {
    const btn = button();
    let refreshed = 0;
    startColdAgent(btn, () => { refreshed += 1; });
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(btn.disabled, false, "the button must be usable again");
    assert.equal(btn.textContent, "Start agent", "…and say what it does");
    assert.equal(refreshed, 0, "a failed start must not refresh as though it worked");
  });
});

test("a rejected start never throws out of the handler", () => {
  // `.catch(...)`. An unhandled rejection inside a delegated click listener surfaces as an unrelated
  // console error and, in some browsers, kills the listener for the rest of the page's life.
  return withFetch({ reject: true }, async () => {
    assert.doesNotThrow(() => startColdAgent(button(), () => {}));
    await new Promise((r) => setTimeout(r, 0));
  });
});

test("switchModeFromChip SUPPRESSES the default and STOPS propagation before switching", () => {
  // Both are load-bearing: the chips sit inside selectable session rows, so a click that propagates also
  // selects the row, and one that defaults may follow an enclosing control. Order matters too — they
  // happen before the switch, so an exception in the switch cannot leave the click half-handled.
  let prevented = 0;
  let stopped = 0;
  const calls = [];
  const event = { preventDefault: () => { prevented += 1; }, stopPropagation: () => { stopped += 1; } };
  switchModeFromChip(
    { dataset: { modeSwitch: "coder-1", targetMode: "managed" } },
    event,
    (id, mode) => calls.push([id, mode]),
  );
  assert.equal(prevented, 1);
  assert.equal(stopped, 1);
  assert.deepEqual(calls, [["coder-1", "managed"]], "agent id and target mode, in that order");
});

test("switchModeFromChip passes through whatever the chip declares, including an absent target", () => {
  // The handler is not the place that validates the mode — the callee is. Silently defaulting here would
  // hide a chip rendered without its target attribute.
  const calls = [];
  switchModeFromChip(
    { dataset: { modeSwitch: "coder-1" } },
    { preventDefault() {}, stopPropagation() {} },
    (id, mode) => calls.push([id, mode]),
  );
  assert.deepEqual(calls, [["coder-1", undefined]]);
});
