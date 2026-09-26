// The stored service key goes only to the origin it was entered for.
//
// THE DEFECT (0.7.1 review, W03). `?apiOrigin=` repoints the dashboard at another server and
// persists that choice, which is what the parameter is for. The key the operator typed was stored
// under one name for every origin, so a link carrying `?apiOrigin=https://receiver…` made the
// dashboard send the operator's key to the receiver on its first request, as a header, and again on
// the realtime socket as a query parameter.
//
// Driven through the modules app.js uses, in the order it uses them: `resolveApiOrigin` picks the
// origin, `setApiBase` seeds the client, `api()` makes the request, `withApiKey` builds the socket
// URL, and a 401 mounts the real prompt.

import assert from "node:assert/strict";
import test from "node:test";

import { resolveApiOrigin, defaultApiOrigin } from "./api-origin.mjs";
import { api, setApiBase } from "./api-client.mjs";
import { adoptKeyFromLocation, adoptLegacyApiKey, readApiKey, resetAdoptionForTests, withApiKey, writeApiKey } from "./api-key.mjs";
import { PROMPT_ID } from "./api-key-prompt.mjs";

const HOME = "http://localhost:8800";
const RECEIVER = "http://receiver.test:9000";

/** A browser at http://localhost:8801 with `search`, a working store, and a fetch that records. */
function browser(t, { search = "", stored = {} } = {}) {
  const saved = Object.fromEntries(["location", "localStorage", "document", "fetch", "history"].map((k) => [k, globalThis[k]]));
  const store = new Map(Object.entries(stored));
  const nodes = new Map();
  const element = () => ({
    style: {}, children: [], attributes: {}, value: "", listeners: {},
    set id(v) { this._id = v; nodes.set(v, this); }, get id() { return this._id; },
    setAttribute(k, v) { this.attributes[k] = v; }, appendChild(c) { this.children.push(c); return c; },
    addEventListener(type, fn) { this.listeners[type] = fn; }, focus() {},
  });
  globalThis.location = { search, protocol: "http:", hostname: "localhost", origin: "http://localhost:8801", href: `http://localhost:8801/${search}` };
  globalThis.history = { replaceState() {} };
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  globalThis.document = {
    documentElement: { dataset: {} },
    body: element(), createElement: element, getElementById: (id) => nodes.get(id) || null,
  };
  const sent = [];
  let status = 200;
  globalThis.fetch = async (url, options = {}) => {
    sent.push({ url: String(url), key: options.headers?.["X-API-Key"] });
    return { ok: status < 400, status, statusText: "", text: async () => (status < 400 ? "{}" : JSON.stringify({ error: "Invalid or missing API key." })) };
  };
  resetAdoptionForTests();
  t.after(() => {
    for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete globalThis[k]; else globalThis[k] = v; }
    setApiBase("");
    resetAdoptionForTests();
  });
  return { store, sent, nodes, refuseNext: () => { status = 401; } };
}

/** What app.js does at load: resolve the origin, then seed the client with it. */
function boot() {
  const origin = resolveApiOrigin();
  setApiBase(`${origin}/api/v1`, origin);
  return origin;
}

test("A LINK THAT REPOINTS THE DASHBOARD DOES NOT SEND THE NEW ORIGIN THE STORED KEY", async (t) => {
  const h = browser(t, { search: `?apiOrigin=${encodeURIComponent(RECEIVER)}` });
  writeApiKey("operator-key", HOME);
  assert.equal(boot(), RECEIVER, "the parameter still repoints the dashboard: that is what it is for");
  await api("/agents");
  assert.equal(h.sent[0].url, `${RECEIVER}/api/v1/agents`);
  assert.equal(h.sent[0].key, undefined, "the key entered for the home service was sent to the receiver");
  assert.doesNotMatch(withApiKey(`${RECEIVER.replace(/^http/, "ws")}/ws?changes=1`), /api_key=/,
    "the realtime socket carried the home service's key to the receiver");
});

test("CONTROL: without the parameter the same stored key is sent to its own origin", async (t) => {
  const h = browser(t);
  writeApiKey("operator-key", HOME);
  assert.equal(boot(), HOME);
  await api("/agents");
  assert.equal(h.sent[0].key, "operator-key", "the key must still reach the service it was entered for");
  assert.match(withApiKey("ws://localhost:8800/ws?changes=1"), /api_key=operator-key/);
});

test("THE NEW ORIGIN ASKS FOR ITS OWN KEY, and storing it leaves the home key where it was", async (t) => {
  const h = browser(t, { search: `?apiOrigin=${encodeURIComponent(RECEIVER)}` });
  writeApiKey("operator-key", HOME);
  boot();
  h.refuseNext();
  await assert.rejects(() => api("/agents"), /Invalid or missing API key/);
  const prompt = h.nodes.get(PROMPT_ID);
  assert.ok(prompt, "a 401 from the new origin must ask for a key");
  const card = prompt.children[0];
  const hint = card.children.find((c) => /API key for/.test(c.textContent || ""));
  assert.ok(hint && hint.textContent.includes(RECEIVER), "the prompt must name the origin the key is for");
  const input = card.children.find((c) => c.type === "password");
  input.value = "receiver-key";
  card.listeners.submit({ preventDefault() {} });
  assert.equal(readApiKey(RECEIVER), "receiver-key");
  assert.equal(readApiKey(HOME), "operator-key", "entering a key for the new origin touched the home service's key");
});

test("A KEY STORED BEFORE KEYS WERE BOUND moves to the dashboard's own default origin, never a linked one", async (t) => {
  // Before 0.7.1 the key sat under one name for every origin. It was entered for the service this
  // page talks to by default, so that is where it goes, and a `?apiOrigin=` cannot redirect it.
  const h = browser(t, { search: `?apiOrigin=${encodeURIComponent(RECEIVER)}`, stored: { "aify.apiKey": "operator-key" } });
  adoptLegacyApiKey(defaultApiOrigin());
  assert.equal(h.store.has("aify.apiKey"), false, "the unbound copy must not stay behind");
  boot();
  await api("/agents");
  assert.equal(h.sent[0].key, undefined, "the pre-0.7.1 key was sent to the linked origin");
  assert.equal(readApiKey(HOME), "operator-key", "CONTROL: it was kept, for the default origin");
});

test("A BOOKMARKED ?api_key= AFTER AN EARLIER ?apiOrigin= LINK IS NOT SENT TO THE STORED ORIGIN", async (t) => {
  // v0.7.2 (external review, item 1). Visit one: a link repoints the dashboard, and the choice is
  // stored. Visit two: the operator's ordinary bookmark carries the key. The stored origin is still in
  // force, and the key went to it. It belongs to the service that served the page.
  const h = browser(t, { search: `?apiOrigin=${encodeURIComponent(RECEIVER)}` });
  assert.equal(boot(), RECEIVER);
  globalThis.location = { search: "?api_key=bookmarked-key", protocol: "http:", hostname: "localhost", origin: "http://localhost:8801", href: "http://localhost:8801/?api_key=bookmarked-key" };
  resetAdoptionForTests();
  adoptKeyFromLocation(defaultApiOrigin()); // what app.js does at boot
  assert.equal(boot(), RECEIVER, "the stored origin is still in force on the second visit");
  await api("/agents");
  assert.equal(h.sent[0].url, `${RECEIVER}/api/v1/agents`);
  assert.equal(h.sent[0].key, undefined, "the bookmarked key was sent to the origin a link stored");
  assert.doesNotMatch(withApiKey(`${RECEIVER.replace(/^http/, "ws")}/ws`), /api_key=/, "the socket carried it there");
  assert.equal(readApiKey(HOME), "bookmarked-key", "CONTROL: the key was kept, for the page's own service");
  setApiBase(`${HOME}/api/v1`, HOME);
  await api("/agents");
  assert.equal(h.sent[1].key, "bookmarked-key", "CONTROL: the page's own service still receives it");
});
