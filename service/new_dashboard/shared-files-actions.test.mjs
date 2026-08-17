// Deleting a shared file: the confirmation gate, the path it deletes, and the row button.
//
// From the dashboard census (78 of 492 named functions never called by this suite). `deleteSharedFile` and
// `deleteSharedFileFromRow` were two of them, uncovered because the delete path awaits `uiConfirm`, which
// builds a real overlay and waits for a click. `withDialog` DRIVES that dialog rather than stubbing it away —
// stubbing the confirm would remove the guard from the test along with the dialog, and "declining does not
// delete" is the only property that really matters on a destructive action.
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
  deleteSharedFile, deleteSharedFileFromRow,
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

// What the last dialog put on screen, and what the action under test returned. Both are recorded because a
// property asserted only through the promise cannot see either: openDialog renders the tone into the overlay's
// markup, and a fire-and-forget action's return value is gone once it has been awaited.
const LAST_DIALOG = { markup: "", returned: "unset" };

// A node that satisfies openDialog: innerHTML, querySelector for the three controls it wires, and
// querySelectorAll for its focus trap. Handlers are captured per selector so a test can fire one.
function dialogAwareNode(handlers) {
  const node = fakeNode();
  // openDialog assigns the whole dialog as markup; keep it so the tone is assertable.
  Object.defineProperty(node, "innerHTML", {
    get: () => LAST_DIALOG.markup,
    set: (html) => { LAST_DIALOG.markup = String(html); },
  });
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
  LAST_DIALOG.markup = "";
  LAST_DIALOG.returned = "unset";
  try {
    const pending = run();
    // Captured BEFORE the await, because "did this return a promise" is a property some callers depend on
    // and `await undefined` is indistinguishable from `await Promise.resolve(undefined)` afterwards.
    LAST_DIALOG.returned = pending;
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

// ── delete ──────────────────────────────────────────────────────────────────

test("DECLINING the delete confirmation sends no request at all", async () => {
  // The entire point of a confirm on a destructive action. If the request went out first, "Cancel" would be
  // a lie and the file would already be gone for everyone.
  respond({ ok: true });
  state.files = [{ name: "keep.txt" }];
  await withDialog({ "files-list": fakeNode() }, false, () => deleteSharedFile("keep.txt"));
  assert.deepEqual(SEEN, [], "a declined confirmation still talked to the service");

  // The dialog itself has to LOOK destructive: `tone: 'danger'` is what makes the confirm button red and
  // adds `dialog-danger`. Without it this is an ordinary-looking prompt for an irreversible action.
  assert.match(LAST_DIALOG.markup, /dialog-danger/, "the delete confirm was not rendered as destructive");
  assert.match(LAST_DIALOG.markup, /keep\.txt/, "the dialog did not name the file being deleted");
  assert.match(LAST_DIALOG.markup, /removes it for everyone/, "the dialog understates what delete does");
});

test("confirming deletes THAT file and then refreshes the list", async () => {
  respond({ ok: true, files: [] });
  state.files = [{ name: "gone.txt" }];
  await withDialog({ "files-list": fakeNode() }, true, () => deleteSharedFile("gone.txt"));

  const del = SEEN.find((r) => r.method === "DELETE");
  assert.ok(del, "no DELETE was sent after confirming");
  assert.equal(del.url, "/api/v1/shared/gone.txt");
  assert.ok(SEEN.some((r) => r.method === "GET" && r.url === "/api/v1/shared"),
    "the list was not reloaded, so the deleted row would stay on screen");
});

test("a filename needing encoding is encoded into the path", async () => {
  // Names come from whoever uploaded them. An unencoded slash addresses a different resource entirely.
  respond({ ok: true, files: [] });
  state.files = [];
  await withDialog({ "files-list": fakeNode() }, true, () => deleteSharedFile("a b/c.txt"));
  assert.equal(SEEN.find((r) => r.method === "DELETE").url, "/api/v1/shared/a%20b%2Fc.txt");
});

test("the row button takes its target from the dataset and swallows the failure", async () => {
  // It runs from the delegated click handler, which cannot await — so it returns nothing and catches its own
  // rejection. Without that catch a failed delete is an unhandled rejection with no caller and no toast.
  respond({ detail: "nope" }, 500);
  state.files = [];
  await withDialog(
    { "files-list": fakeNode() },
    true,
    () => deleteSharedFileFromRow({ dataset: { fileDelete: "boom.txt" } }),
    () => SEEN.some((r) => r.method === "DELETE"),
  );
  assert.equal(SEEN.find((r) => r.method === "DELETE").url, "/api/v1/shared/boom.txt",
    "the dataset value did not become the target");
});

test("the row button returns nothing — it is fire-and-forget by design", async () => {
  respond({ ok: true, files: [] });
  state.files = [];
  await withDialog(
    { "files-list": fakeNode() },
    true,
    () => deleteSharedFileFromRow({ dataset: { fileDelete: "x.txt" } }),
    () => SEEN.some((r) => r.method === "DELETE"),
  );
  // Read from the pre-await capture: `await undefined` and `await Promise.resolve(undefined)` are the same
  // value, so asserting on the awaited result cannot tell a fire-and-forget call from one that returns work.
  assert.equal(LAST_DIALOG.returned, undefined,
    "it now returns a promise — a caller could await it, and this test should say so instead");
});
