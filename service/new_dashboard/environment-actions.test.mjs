// The four things an operator can DO to an environment, tested by CALLING them.
//
// They live in `environments-panels.mjs` beside the panels that render them — one subject — but get
// their own test file because their concerns are the opposite of a renderer's: what reaches the server.
//
// `createSpawnRequest` is the one that matters most. It is how a new managed worker comes into
// existence, every field it reads is a free-text input, and a spawn that is missing a workspace
// produces a worker with nowhere to run — a zombie that strands whatever is dispatched to it. So its
// required-field gate is pinned field by field rather than once.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import {
  controlEnvironment,
  createSpawnRequest,
  initEnvironmentActions,
  resetEnvironmentRoots,
  submitEnvironmentRoots,
} from "./environments-panels.mjs";

function makeEl(extra = {}) {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, disabled: false,
    dataset: {}, style: {}, children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, setAttribute() {},
    addEventListener() {}, removeEventListener() {}, remove() {}, focus() {},
    ...extra,
  };
}

function makeDialog(answer, typed) {
  const listeners = new Map();
  const button = (key) => makeEl({ addEventListener: (ev, fn) => { if (ev === "click") listeners.set(key, fn); } });
  const confirmBtn = button("confirm");
  const cancelBtn = button("cancel");
  const input = makeEl({ value: typed ?? "" });
  const overlay = makeEl({
    querySelector: (sel) => ({ ".dialog-confirm": confirmBtn, ".dialog-cancel": cancelBtn, ".dialog-input": input }[sel] ?? null),
    querySelectorAll: () => [cancelBtn, confirmBtn],
  });
  overlay.__answer = () => {
    input.value = typed ?? "";
    const fn = listeners.get(answer ? "confirm" : "cancel");
    if (fn) fn();
  };
  return overlay;
}

/** `fields` maps element id -> value, which is how the spawn form is filled in. */
function withEnvActions({ confirm = true, prompt = "typed", fields = {} } = {}) {
  const els = new Map();
  const sent = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => {
      if (!els.has(id)) els.set(id, makeEl({ value: fields[id] ?? "" }));
      return els.get(id);
    },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => makeDialog(confirm, prompt),
    addEventListener() {}, removeEventListener() {},
    body: {
      appendChild: (el) => { if (el && el.className === "dialog-overlay") queueMicrotask(() => el.__answer()); },
      classList: { add() {}, remove() {} }, style: { setProperty() {} },
    },
    activeElement: null,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method !== "GET") sent.push({ url: String(url), method, body: options.body });
    const payload = { ok: true, spawnRequest: { id: "sr1" }, environment: {} };
    return { ok: true, status: 200, statusText: "OK", json: async () => payload, text: async () => JSON.stringify(payload) };
  };
  setApiBase("");
  const calls = { refresh: 0, refreshSoon: 0, closeInspector: 0, inspected: [] };
  initEnvironmentActions({
    closeInspector: () => { calls.closeInspector += 1; },
    inspect: (kind, payload) => { calls.inspected.push([kind, payload]); },
    refresh: async () => { calls.refresh += 1; },
    refreshSoon: () => { calls.refreshSoon += 1; },
  });
  return { els, sent, calls, restore: () => Object.assign(globalThis, saved) };
}

const COMPLETE = {
  "env-spawn-environment": "env-1",
  "env-spawn-runtime": "claude",
  "env-spawn-agent-id": "new-coder",
  "env-spawn-role": "coder",
  "env-spawn-workspace": "C:/work/proj",
  "env-spawn-prompt": "get started",
};

test("EACH REQUIRED FIELD IS REJECTED ON ITS OWN", async () => {
  // Dropped one at a time rather than all together: a gate written with the wrong operator still
  // rejects the all-empty case a single test would have used, and passes every real one.
  for (const missing of ["env-spawn-environment", "env-spawn-runtime", "env-spawn-agent-id", "env-spawn-workspace"]) {
    const h = withEnvActions({ fields: { ...COMPLETE, [missing]: "" } });
    try {
      await createSpawnRequest();
      assert.deepEqual(h.sent, [], `a spawn without ${missing} must not be sent`);
    } finally { h.restore(); }
  }
});

test("WHITESPACE IS NOT A WORKSPACE", async () => {
  // These are free-text inputs. Trimming on the read is what stops "   " counting as filled in, and a
  // whitespace workspace fails far away from here — at launch, as a worker that never starts.
  const h = withEnvActions({ fields: { ...COMPLETE, "env-spawn-workspace": "   " } });
  try {
    await createSpawnRequest();
    assert.deepEqual(h.sent, []);
  } finally { h.restore(); }
});

test("a whitespace AGENT ID is refused too", async () => {
  const h = withEnvActions({ fields: { ...COMPLETE, "env-spawn-agent-id": "  " } });
  try {
    await createSpawnRequest();
    assert.deepEqual(h.sent, []);
  } finally { h.restore(); }
});

test("a COMPLETE spawn posts every field the operator typed", async () => {
  const h = withEnvActions({ fields: COMPLETE });
  try {
    await createSpawnRequest();
    assert.equal(h.sent.length, 1);
    assert.match(h.sent[0].url, /\/spawn-requests$/);
    const body = JSON.parse(h.sent[0].body);
    assert.equal(body.environmentId, "env-1");
    assert.equal(body.runtime, "claude");
    assert.equal(body.agentId, "new-coder");
    assert.equal(body.role, "coder");
    assert.equal(body.workspace, "C:/work/proj");
    assert.equal(body.initialMessage, "get started");
    assert.equal(body.mode, "managed-warm");
    assert.equal(body.createdBy, "dashboard");
  } finally { h.restore(); }
});

test("THE OPTIONAL PROMPT MAY BE EMPTY, and then carries no subject", async () => {
  // The subject is derived from the prompt. An unconditional template would send the literal
  // "Spawn new-coder" as the subject of a message that does not exist.
  const h = withEnvActions({ fields: { ...COMPLETE, "env-spawn-prompt": "" } });
  try {
    await createSpawnRequest();
    const body = JSON.parse(h.sent[0].body);
    assert.equal(body.initialMessage, "");
    assert.equal(body.subject, "", "no prompt means no subject");
  } finally { h.restore(); }
});

test("a spawn WITH a prompt gets a subject naming the agent", async () => {
  const h = withEnvActions({ fields: COMPLETE });
  try {
    await createSpawnRequest();
    assert.match(JSON.parse(h.sent[0].body).subject, /new-coder/);
  } finally { h.restore(); }
});

test("a successful spawn CLEARS the identity fields but keeps the workspace", async () => {
  // Agent ids are unique, so leaving the id in place invites a second click that fails on a collision
  // whose cause is invisible. The workspace stays because the next spawn is usually in the same place.
  const h = withEnvActions({ fields: COMPLETE });
  try {
    await createSpawnRequest();
    assert.equal(h.els.get("env-spawn-agent-id").value, "");
    assert.equal(h.els.get("env-spawn-prompt").value, "");
    assert.equal(h.els.get("env-spawn-workspace").value, "C:/work/proj");
  } finally { h.restore(); }
});

test("a successful spawn refreshes, so the queue shows the request that was just made", async () => {
  const h = withEnvActions({ fields: COMPLETE });
  try {
    await createSpawnRequest();
    assert.equal(h.calls.refresh, 1);
    assert.equal(h.calls.inspected.length, 1, "the created request must be shown, not just queued silently");
  } finally { h.restore(); }
});

test("controlEnvironment posts the action for the NAMED environment", async () => {
  const h = withEnvActions({ confirm: true });
  try {
    await controlEnvironment("env-1", "restart");
    assert.equal(h.sent.length, 1);
    assert.match(h.sent[0].url, /env-1/);
  } finally { h.restore(); }
});

test("submitting roots SPLITS on newlines AND commas, and drops blanks", async () => {
  // The editor is a free-text box. Both separators are accepted because operators paste either, and a
  // trailing newline would otherwise send an empty root — which, not being a path, silently narrows
  // what may be spawned rather than doing nothing.
  const h = withEnvActions({ confirm: true, fields: { "env-edit-roots": "C:/a\n C:/b ,C:/c\n\n" } });
  try {
    await submitEnvironmentRoots("env-1");
    assert.equal(h.sent.length, 1);
    assert.match(h.sent[0].url, /environments\/env-1\/roots/);
    assert.deepEqual(JSON.parse(h.sent[0].body).roots, ["C:/a", "C:/b", "C:/c"]);
  } finally { h.restore(); }
});

test("SUBMITTING AN EMPTY ROOT LIST IS REFUSED, and points at the reset instead", async () => {
  // The security-shaped one. `workspaceWithinRoots` fails OPEN with no roots, so sending an empty list
  // does not "clear" the restriction — it removes it. Clearing the box and saving must therefore not
  // be a way to get there by accident.
  for (const text of ["", "   ", "\n\n", " , , "]) {
    const h = withEnvActions({ confirm: true, fields: { "env-edit-roots": text } });
    try {
      await submitEnvironmentRoots("env-1");
      assert.deepEqual(h.sent, [], `${JSON.stringify(text)} must not be sent as a roots list`);
    } finally { h.restore(); }
  }
});

test("RESETTING ROOTS ASKS FOR THE BRIDGE-ADVERTISED SET, not an empty list", async () => {
  // An empty roots list is not the same as "whatever the bridge says". `workspaceWithinRoots` fails
  // OPEN when the list is empty, so sending [] would quietly widen what may be spawned rather than
  // restoring a default.
  const h = withEnvActions({ confirm: true });
  try {
    await resetEnvironmentRoots("env-1");
    assert.equal(h.sent.length, 1);
    assert.match(String(h.sent[0].body ?? ""), /resetToBridgeAdvertised/);
  } finally { h.restore(); }
});

test("INIT REFUSES A PARTIAL BAG", () => {
  const full = { closeInspector() {}, inspect() {}, refresh: async () => {}, refreshSoon() {} };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initEnvironmentActions(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.doesNotThrow(() => initEnvironmentActions(full));
});
