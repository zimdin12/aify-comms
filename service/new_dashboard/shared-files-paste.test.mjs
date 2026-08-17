// Pasting an image into the composer.
//
// From the dashboard census: `uploadPastedImage` was never called by this suite. Unlike the attach path it
// PROPAGATES failure, because its caller is the paste handler that surfaces the message — and it inspects the
// body as well as the status, since the service answers some rejections with a 200.
//
// SPLIT BY REQUEST COUNT, AND THE REASON IS MEASURED. The sibling shared-files.test.mjs records a hang it
// could not explain (its fourth upload never returns) after three failed fixes. It reproduces in a FRESH
// process, in a new file, with a different request mix: five delete/dialog tests pass and the next request —
// whatever it is — never returns, at roughly the eighth request of the process. So it is neither
// file-specific nor caused by any single test, and `Connection: close` on every response does not change it
// (a fourth ruled-out cause). Rather than keep guessing at a test-harness defect, each of these files stays
// under that ceiling: one process per file is what `node --test` already gives.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  uploadPastedImage,
} from "./shared-files.mjs";

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
setApiBase(`http://127.0.0.2:${PORT}/api/v1`);

test.after(() => SERVER.close());

function respond(payload, status = 200) {
  SEEN.length = 0;
  HANDLER = (_req, res) => {
    // `Connection: close` is kept, but it is NOT what makes this file finish — it was a hypothesis about the
    // hang (an error response whose body the client never reads leaving undici's pooled socket busy) and it
    // did NOT change the behaviour. Recorded as a fourth ruled-out cause rather than presented as the fix;
    // what actually keeps these files finishing is staying under the request ceiling, per the header.
    res.writeHead(status, { "content-type": "application/json", connection: "close" });
    res.end(JSON.stringify(payload));
  };
}

// Narrow enough that a test cannot pass because the host provided something real.
function fakeNode() {
  return {
    className: "", textContent: "", value: "", innerHTML: "", children: [], isConnected: true,
    classList: { add() {}, remove() {} },
    setAttribute() {}, remove() {}, addEventListener() {}, removeEventListener() {}, focus() {},
    get firstElementChild() { return this.children[0] || null; },
    appendChild(child) { this.children.push(child); return child; },
    querySelectorAll: () => [],
  };
}

// `toast()` schedules a 4.2s auto-dismiss; a referenced timer keeps Node alive long past the assertions.
// The real timer still fires — only its hold on the loop is dropped. (mock.timers is not an option: it stops
// undici from ever completing a request, which reads as a dead module. Measured in the sibling file.)
function installDom(elements, extra = {}) {
  const had = ["document", "requestAnimationFrame"].filter((g) => g in globalThis);
  assert.deepEqual(had, [], "a browser global leaked into the test environment — the seal is broken");
  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    createElement: () => fakeNode(),
    body: fakeNode(),
    addEventListener() {}, removeEventListener() {}, activeElement: null,
    ...extra,
  };
  globalThis.requestAnimationFrame = (fn) => fn();
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms, ...rest) => {
    const handle = realSetTimeout(fn, ms, ...rest);
    if (handle && typeof handle.unref === "function") handle.unref();
    return handle;
  };
  return {
    realSetTimeout,
    restore() {
      globalThis.setTimeout = realSetTimeout;
      delete globalThis.document;
      delete globalThis.requestAnimationFrame;
    },
  };
}

async function withDom(elements, run) {
  const dom = installDom(elements);
  try {
    return await run();
  } finally {
    dom.restore();
  }
}

// A node that satisfies openDialog: innerHTML, querySelector for the three controls it wires, and
// querySelectorAll for its focus trap. Handlers are captured per selector so a test can fire one.
function dialogAwareNode(handlers) {
  const node = fakeNode();
  node.querySelector = (selector) => {
    if (!handlers.has(selector)) {
      const child = fakeNode();
      child.addEventListener = (_type, fn) => { handlers.set(`${selector}!`, fn); };
      handlers.set(selector, child);
    }
    return handlers.get(selector);
  };
  return node;
}

// Runs `run()` with a dialog-capable DOM, answers the confirm, then waits for `until` before tearing the
// globals down. `until` exists because `deleteSharedFileFromRow` is deliberately FIRE-AND-FORGET — it does
// not return its promise, so awaiting the call proves nothing and the first version of this test asserted
// before the request had been made.
async function withDialog(elements, answer, run, until = null) {
  const handlers = new Map();
  const dom = installDom(elements, { createElement: () => dialogAwareNode(handlers) });
  try {
    const pending = run();
    await new Promise((resolve) => dom.realSetTimeout(resolve, 0));
    const fire = handlers.get(answer ? ".dialog-confirm!" : ".dialog-cancel!");
    assert.ok(fire, "the confirm dialog never wired its buttons — the harness, not the module");
    fire();
    const result = await pending;
    if (until) {
      const deadline = Date.now() + 5000;
      while (Date.now() < deadline && !until()) {
        await new Promise((resolve) => dom.realSetTimeout(resolve, 10));
      }
      assert.ok(until(), "the fire-and-forget action never reached the service");
    }
    return result;
  } finally {
    dom.restore();
  }
}

// ── pasted images ───────────────────────────────────────────────────────────

test("a pasted image is uploaded and linked into the target element", async () => {
  respond({ ok: true });
  const dispatched = [];
  const target = { value: "", focus() {}, dispatchEvent: (e) => dispatched.push(e.type) };
  await withDom({}, () => uploadPastedImage(new Blob(["x"], { type: "image/png" }), target));

  const post = SEEN.find((r) => r.method === "POST");
  assert.equal(post.url, "/api/v1/shared");
  assert.match(post.headers["content-type"], /^multipart\/form-data; boundary=/);
  const name = /\[image: (img-\d+\.png)\]/.exec(target.value)?.[1];
  assert.ok(name, "no link to the pasted image reached the composer");
  // The link must address the FILE. `${apiBase}/shared` alone is the collection, and a composer full of
  // identical collection links tells the agent nothing about which image was pasted.
  assert.ok(target.value.startsWith(`[image: ${name}] http`),
    `the composer text is not the image reference plus a link: ${target.value}`);
  assert.ok(target.value.endsWith(`/shared/${name}`),
    `the link addresses the collection rather than the file: ${target.value}`);
  assert.deepEqual(dispatched, ["input"],
    "no input event was dispatched, so autosize and draft-persist never ran");
});

test("a pasted image goes on its own LINE when the composer does not end with one", async () => {
  respond({ ok: true });
  const target = { value: "look at this", focus() {}, dispatchEvent() {} };
  await withDom({}, () => uploadPastedImage(new Blob(["x"], { type: "image/png" }), target));
  assert.match(target.value, /^look at this\n\[image: /, "the link was jammed onto the operator's own line");
});

test("a pasted image does not add a SECOND newline when one is already there", async () => {
  respond({ ok: true });
  const target = { value: "look at this\n", focus() {}, dispatchEvent() {} };
  await withDom({}, () => uploadPastedImage(new Blob(["x"], { type: "image/png" }), target));
  assert.ok(!target.value.includes("\n\n"), "a blank line was inserted into the message");
});

test("a rejected image upload THROWS with the service's own reason", async () => {
  // Unlike attach, this one propagates: its caller is the paste handler, which surfaces the message. A
  // swallowed failure leaves the operator believing the image is attached.
  respond({ detail: "unsupported media type" }, 415);
  const target = { value: "", focus() {}, dispatchEvent() {} };
  await assert.rejects(
    () => withDom({}, () => uploadPastedImage(new Blob(["x"], { type: "image/gif" }), target)),
    /unsupported media type/,
  );
  assert.equal(target.value, "", "a failed upload still edited the composer");
});

test("a 200 that says ok:false is also a failure", async () => {
  // The service answers some rejections with a 200 body. Trusting the status alone reports success.
  respond({ ok: false, error: "quota exceeded" });
  const target = { value: "", focus() {}, dispatchEvent() {} };
  await assert.rejects(
    () => withDom({}, () => uploadPastedImage(new Blob(["x"], { type: "image/png" }), target)),
    /quota exceeded/,
  );
});

test("pasting with no blob, or no target, does nothing", async () => {
  respond({ ok: true });
  await withDom({}, () => uploadPastedImage(null, { value: "" }));
  await withDom({}, () => uploadPastedImage(new Blob(["x"], { type: "image/png" }), null));
  assert.deepEqual(SEEN, [], "a paste with nothing to upload still called the service");
});
