// Attaching a shared file to a chat message.
//
// From the dashboard census: `attachChatFile` was never called by this suite. It uploads multipart and then
// edits the operator's composer, so the two properties worth pinning are that the JSON default is cleared
// (or the service cannot read the body) and that a FAILED upload leaves the draft alone — a `[shared:...]`
// marker for a file that is not on the server is a reference the agent cannot resolve.
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
  attachChatFile,
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

// ── attach to chat ──────────────────────────────────────────────────────────

test("attaching a chat file uploads multipart and references it in the composer", async () => {
  respond({ ok: true });
  const body = { value: "", focus() {} };
  state.chat = { identity: "dashboard", selected: "agent-a", drafts: {} };
  state.files = [];

  await withDom({ "chat-composer-body": body, "files-list": fakeNode() },
    () => attachChatFile(new File(["hi"], "note.txt", { type: "text/plain" })));

  const post = SEEN.find((r) => r.method === "POST");
  assert.match(post.headers["content-type"], /^multipart\/form-data; boundary=/,
    "the JSON default was not cleared — the service cannot read this body");
  // The FILE part specifically — `name` is also a form field, so matching the filename alone passed even
  // with the file part removed entirely.
  assert.match(post.body, /filename="note\.txt"/, "the file itself was not part of the upload");
  assert.match(post.body, /name="file"/, "no field named `file` reached the service");
  assert.equal(body.value, "[shared:note.txt]", "the composer got no reference to the file just attached");
  assert.equal(state.chat.drafts["agent-a"], "[shared:note.txt]",
    "the reference was not persisted, so switching conversations would lose it");
});

test("an attachment APPENDS to whatever the operator had already typed", async () => {
  respond({ ok: true });
  const body = { value: "please look at", focus() {} };
  state.chat = { identity: "dashboard", selected: "agent-a", drafts: {} };
  state.files = [];
  await withDom({ "chat-composer-body": body, "files-list": fakeNode() },
    () => attachChatFile(new File(["hi"], "note.txt")));
  assert.equal(body.value, "please look at [shared:note.txt]", "the operator's draft was overwritten");
});

test("a FAILED attach leaves the composer untouched", async () => {
  // The reference means "this file is on the server". Appending it after a failed upload puts a dead
  // `[shared:...]` marker into a message the agent then cannot resolve.
  respond({ detail: "too big" }, 413);
  const body = { value: "draft text", focus() {} };
  state.chat = { identity: "dashboard", selected: "agent-a", drafts: {} };
  state.files = [];
  await withDom({ "chat-composer-body": body, "files-list": fakeNode() },
    () => attachChatFile(new File(["hi"], "note.txt")));
  assert.equal(body.value, "draft text", "a failed upload still edited the composer");
  assert.equal(state.chat.drafts["agent-a"], undefined, "…and still persisted a draft for it");
});

test("attaching nothing makes no request", async () => {
  respond({ ok: true });
  await withDom({}, () => attachChatFile(null));
  assert.deepEqual(SEEN, []);
});
