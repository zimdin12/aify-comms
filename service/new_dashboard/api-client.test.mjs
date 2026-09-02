// Real tests for the dashboard's HTTP wrapper, extracted from app.js in v0.5.4.
//
// EVERY REQUEST THE DASHBOARD MAKES GOES THROUGH THIS FUNCTION — 74 call sites — and none of it had a
// test. Its error path is the interesting half: FastAPI returns validation failures as `detail`, an ARRAY
// of {loc,msg,...}, and the original code did `data.detail` straight into an Error, which rendered as
// "[object Object]" in a toast. The flattening that fixed it is the kind of thing that silently regresses,
// so all four shapes it has to handle are pinned here.
//
// A REAL LOOPBACK SERVER on 127.0.0.2 rather than a stubbed `fetch`: the point of extracting this module
// was that it can be imported and exercised in Node, and stubbing the one call it makes would leave that
// unproven. Node's global fetch does the request for real.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { api, currentApiBase, setApiBase, setOperatorKey } from "./api-client.mjs";

let HANDLER = (_req, res) => { res.writeHead(200); res.end("{}"); };
const SEEN = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    SEEN.push({ url: req.url, method: req.method, headers: req.headers, body });
    HANDLER(req, res);
  });
});

const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
// UNREF: node --test waits on an open handle, so a listening server makes the file time out
// rather than fail — a hang reads as nothing at all.
SERVER.unref();
const BASE = `http://127.0.0.2:${PORT}/api/v1`;
setApiBase(BASE);

test.after(() => SERVER.close());

function respond(status, payload, { raw = false } = {}) {
  SEEN.length = 0;
  HANDLER = (_req, res) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(raw ? payload : JSON.stringify(payload));
  };
}

test("the seeded base is what requests are built on", async () => {
  // app.js seeds this once at startup. Without the seeding call the module would send every request to a
  // relative URL and Node's fetch would reject outright.
  assert.equal(currentApiBase(), BASE);
  respond(200, { ok: true });
  const data = await api("/agents");
  assert.deepEqual(data, { ok: true });
  assert.equal(SEEN[0].url, "/api/v1/agents", "path is appended to the base verbatim");
});

test("a JSON body comes back parsed", async () => {
  respond(200, { agents: { a1: { role: "coder" } } });
  assert.deepEqual(await api("/agents"), { agents: { a1: { role: "coder" } } });
});

test("an EMPTY body is an empty object, not a parse error", async () => {
  // 204s and empty 200s are ordinary here. `text ? JSON.parse(text) : {}` — without the guard every
  // no-content response would throw "Unexpected end of JSON input" from a successful request.
  respond(200, "", { raw: true });
  assert.deepEqual(await api("/messages/read"), {});
});

test("requests send JSON content-type by default", async () => {
  respond(200, {});
  await api("/agents");
  assert.equal(SEEN[0].headers["content-type"], "application/json");
});

test("`headers: {}` REPLACES the JSON content-type — this is what makes file upload work", async () => {
  // I first wrote this as a footgun. It is the opposite: the spread order is load-bearing. The two upload
  // call sites pass `headers: {}` DELIBERATELY, because a FormData body needs
  // `multipart/form-data; boundary=…` and only fetch can generate the boundary. Were `headers` merged
  // instead of replaced, uploads would go out labelled application/json with no boundary and the service
  // could not parse them.
  respond(200, {});
  const form = new FormData();
  form.set("file", new Blob(["hello"]), "note.txt");
  await api("/shared", { method: "POST", body: form, headers: {} });

  const sent = SEEN[0].headers["content-type"];
  assert.match(sent, /^multipart\/form-data; boundary=/,
    "fetch must be left free to set the multipart content-type and its boundary");
  assert.equal(SEEN[0].method, "POST");
});

test("a caller's own headers replace the default rather than merging with it", async () => {
  // The general form of the rule above: `{ headers: {…}, ...options }`. Note the content-type here is
  // text/plain — NOT the module's application/json and NOT absent — because once the default is replaced
  // fetch supplies its own for a string body. Anyone passing custom headers with a JSON string body would
  // be sending it as text/plain, so the two current callers passing `headers: {}` with FormData are the
  // only shape this is safe for.
  respond(200, {});
  await api("/agents", { method: "POST", headers: { "x-custom": "1" }, body: "{}" });
  assert.equal(SEEN[0].headers["x-custom"], "1");
  assert.equal(SEEN[0].headers["content-type"], "text/plain;charset=UTF-8");
});

test("a FastAPI validation array is flattened to readable text, not [object Object]", async () => {
  // THE BUG THIS CODE EXISTS FOR. `detail` is an array of {loc,msg}; the original threw `data.detail`
  // directly and the toast read "[object Object]".
  respond(422, { detail: [{ loc: ["body", "agentId"], msg: "field required" }, { msg: "too long" }] });
  await assert.rejects(() => api("/messages/send"), (error) => {
    assert.equal(error.message, "field required; too long", "each msg, joined with '; '");
    return true;
  });
});

test("an array entry with no msg falls back to its JSON rather than vanishing", async () => {
  // `(d && d.msg) ? d.msg : JSON.stringify(d)`. Dropping an entry would under-report what failed.
  respond(422, { detail: [{ loc: ["body"], type: "missing" }] });
  await assert.rejects(() => api("/x"), (error) => {
    assert.equal(error.message, JSON.stringify({ loc: ["body"], type: "missing" }));
    return true;
  });
});

test("a non-array object detail is stringified rather than coerced", async () => {
  respond(500, { detail: { code: "boom" } });
  await assert.rejects(() => api("/x"), (error) => {
    assert.equal(error.message, JSON.stringify({ code: "boom" }));
    return true;
  });
});

test("`error` is preferred over `detail`", async () => {
  // The service uses `error` for its own failures and `detail` is FastAPI's. When both appear the
  // service's own wording is the more specific one.
  respond(400, { error: "agent not found", detail: [{ msg: "ignored" }] });
  await assert.rejects(() => api("/x"), (error) => {
    assert.equal(error.message, "agent not found");
    return true;
  });
});

test("with neither field the HTTP status text is used, so an error is never empty", async () => {
  respond(503, {});
  await assert.rejects(() => api("/x"), (error) => {
    assert.ok(error.message.length > 0, "an errorless failure must still say something");
    return true;
  });
});

test("a 2xx is never treated as an error even when it carries an `error` key", async () => {
  // `response.ok` decides, not the body. A successful response that happens to contain the word error
  // must not be thrown.
  respond(200, { error: null, ok: true });
  assert.deepEqual(await api("/x"), { error: null, ok: true });
});

test("the module imports with no browser globals and no load-time request", async () => {
  const again = await import("./api-client.mjs");
  assert.equal(again.api, api, "one module instance, no load-time side effects");
});

test("setOperatorKey attaches the operator key to every request", async () => {
  // R5-H1 (2026-08-18): naming yourself "operator" no longer grants anything — the service verifies
  // this header. If the dashboard stops sending it, its delete controls 403 with no other symptom.
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    seen.push(options?.headers || {});
    return { ok: true, status: 200, text: async () => "{}" };
  };
  try {
    setApiBase("http://127.0.0.2:1/api/v1", "http://127.0.0.2:1");
    setOperatorKey("k-123");
    await api("/whatever");
    assert.equal(seen[0]["X-Aify-Operator-Key"], "k-123",
      "the operator key was not sent, so every operator-only action will 403");

    // …and a caller supplying its OWN headers still gets it. That path is how file upload drops the
    // JSON content-type, so it must not be the path that loses the credential.
    await api("/upload", { method: "POST", headers: {} });
    assert.equal(seen[1]["X-Aify-Operator-Key"], "k-123",
      "a caller-supplied headers object dropped the operator key");
    assert.equal(seen[1]["Content-Type"], undefined,
      "the caller's empty headers must still REPLACE the JSON content-type — multipart upload "
      + "depends on it");

    setOperatorKey("");
    await api("/none");
    assert.equal(seen[2]["X-Aify-Operator-Key"], undefined,
      "an unset key must not send an empty header");
  } finally {
    globalThis.fetch = realFetch;
  }
});


// --- The service key, and the 401 that asks for it -----------------------------------------------
// THE HELPER BEING RIGHT IS NOT THE CLAIM. `api-key.test.mjs` proves the store and the header shape;
// these prove `api()` actually CALLS them. This repo has shipped a feature whose six helper tests
// were green while the call site was disconnected, so the call site gets its own tests.

import { readApiKey as _readKey } from "./api-key.mjs";
import { PROMPT_ID as _PROMPT_ID } from "./api-key-prompt.mjs";

function _storeWith(value) {
  const data = value ? { "aify.apiKey": value } : {};
  globalThis.localStorage = {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
  return data;
}

function _fakeDocument() {
  const byId = new Map();
  const make = (tag) => ({
    tagName: tag, style: { cssText: "" }, children: [], attributes: {}, value: "", _listeners: {},
    set id(v) { this._id = v; byId.set(v, this); },
    get id() { return this._id; },
    setAttribute(k, v) { this.attributes[k] = v; },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(t, fn) { this._listeners[t] = fn; },
    focus() {},
  });
  return { body: make("body"), createElement: make, getElementById: (id) => byId.get(id) || null };
}

test("the stored service key is sent as X-API-Key on every request", async () => {
  // Without this the dashboard cannot authenticate at all once API_KEY is set: it is served from the
  // dashboard port and calls the API on the service port, so the cookie the service issues does not
  // ride the request and the header is the only carrier left.
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    seen.push(options?.headers || {});
    return { ok: true, status: 200, text: async () => "{}" };
  };
  try {
    _storeWith("banana");
    setApiBase("http://127.0.0.2:1/api/v1", "http://127.0.0.2:1");
    await api("/whatever");
    assert.equal(seen[0]["X-API-Key"], "banana",
      "the service key was not sent, so every request 401s and the dashboard never loads");

    // The same trap the operator key has: a caller replacing the headers wholesale must not lose it.
    await api("/upload", { method: "POST", headers: {} });
    assert.equal(seen[1]["X-API-Key"], "banana",
      "a caller-supplied headers object dropped the service key");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("no stored key sends no header, so an unprotected service is unaffected", async () => {
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    seen.push(options?.headers || {});
    return { ok: true, status: 200, text: async () => "{}" };
  };
  try {
    _storeWith(null);
    setApiBase("http://127.0.0.2:1/api/v1", "http://127.0.0.2:1");
    await api("/whatever");
    assert.equal(seen[0]["X-API-Key"], undefined, "an absent key must not send an empty header");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("a 401 puts the key prompt on the page instead of only throwing", async () => {
  // THE WHOLE POINT. Before this, a keyed service rendered a dashboard that polled, failed, retried
  // and gave the operator no way in except hand-editing the URL.
  const realFetch = globalThis.fetch;
  const realDoc = globalThis.document;
  globalThis.fetch = async () => ({
    ok: false, status: 401, statusText: "Unauthorized",
    text: async () => JSON.stringify({ error: "Invalid or missing API key." }),
  });
  const doc = _fakeDocument();
  globalThis.document = doc;
  try {
    _storeWith("the-wrong-one");
    setApiBase("http://127.0.0.2:1/api/v1", "http://127.0.0.2:1");
    assert.equal(doc.getElementById(_PROMPT_ID), null, "CONTROL: the prompt must not be there yet");
    await assert.rejects(() => api("/whatever"), /Invalid or missing API key/,
      "the error must still propagate -- callers render it");
    assert.notEqual(doc.getElementById(_PROMPT_ID), null,
      "a 401 did not mount the prompt, so the operator has no way to supply a key");
    assert.equal(_readKey(), "",
      "the refused key survived, so it would be retried on every future load");
  } finally {
    globalThis.fetch = realFetch;
    globalThis.document = realDoc;
  }
});

test("a NON-401 failure does not mount the prompt", async () => {
  // NEGATIVE CONTROL. A prompt that appeared on any error would cover the dashboard whenever the
  // service hiccupped, and would read as an auth problem when it is not one.
  const realFetch = globalThis.fetch;
  const realDoc = globalThis.document;
  globalThis.fetch = async () => ({
    ok: false, status: 500, statusText: "Server Error", text: async () => "{}",
  });
  const doc = _fakeDocument();
  globalThis.document = doc;
  try {
    _storeWith("banana");
    setApiBase("http://127.0.0.2:1/api/v1", "http://127.0.0.2:1");
    await assert.rejects(() => api("/whatever"));
    assert.equal(doc.getElementById(_PROMPT_ID), null, "a 500 mounted the key prompt");
    assert.equal(_readKey(), "banana", "a 500 discarded a key that was never refused");
  } finally {
    globalThis.fetch = realFetch;
    globalThis.document = realDoc;
  }
});
