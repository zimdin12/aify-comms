// Real tests for the shared-files surface, extracted from app.js in v0.5.4.
//
// The upload paths are the reason this is worth testing. Two of them build a multipart request by hand and
// pass `headers: {}` to clear the JSON default — if that ever became a merge, uploads would go out
// labelled application/json with no boundary and the service could not read them. And `renderFiles` builds
// a download link by string concatenation from names the UPLOADER chose, so escaping there is the
// difference between a filename and injected markup.
//
// SEALING. `state` is a shared singleton and the browser globals do not exist in Node; each is installed
// per test and removed afterwards, and the DOM/// storage helpers assert they were absent first.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  loadFiles, pastedImageName, renderFiles, uploadSharedFile,
} from "./shared-files.mjs";

let HANDLER = (_req, res) => { res.writeHead(200); res.end("{}"); };
const SEEN = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => { SEEN.push({ url: req.url, method: req.method, headers: req.headers, body }); HANDLER(req, res); });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
const BASE = `http://127.0.0.2:${PORT}/api/v1`;
setApiBase(BASE);

test.after(() => SERVER.close());

function respond(payload, status = 200) {
  SEEN.length = 0;
  HANDLER = (_req, res) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(payload)); };
}

// A DOM stub narrow enough that a test cannot pass because the host provided something.
//
// It covers `getElementById` plus the little `toast()` needs — createElement, a body to append to, and
// requestAnimationFrame — because the upload paths announce their result through toast and would
// otherwise throw before reaching the assertion. `uiConfirm` is NOT faked: it opens a real dialog and
// awaits a click, which is why the delete path is not covered here at all -- see the note below.
function fakeNode() {
  const node = {
    className: "", textContent: "", value: "", children: [], isConnected: true,
    classList: { add() {}, remove() {} },
    setAttribute() {}, remove() {}, addEventListener() {}, removeEventListener() {},
    get firstElementChild() { return this.children[0] || null; },
    appendChild(child) { this.children.push(child); return child; },
  };
  return node;
}

function withDom(elements, run) {
  const had = ["document", "requestAnimationFrame"].filter((g) => g in globalThis);
  assert.deepEqual(had, [], "a browser global leaked into the test environment — the seal is broken");
  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    createElement: () => fakeNode(),
    body: fakeNode(),
  };
  globalThis.requestAnimationFrame = (fn) => fn();

  // UNREF EVERY TIMER THESE PATHS CREATE, without replacing them.
  //
  // `toast()` schedules a 4.2s auto-dismiss. It is UI behaviour nothing here asserts, but a referenced
  // timer keeps Node alive, so the file took ~25s and then TIMED OUT rather than finishing — measured, not
  // guessed: the same tests without the toast-invoking paths run in 141ms, and one upload test alone takes
  // 4.5s. Faking timers outright is not an option: `mock.timers` stops undici's HTTP client from ever
  // completing a request, which reads as a dead module. So the real timer is created and still fires —
  // only its hold on the event loop is dropped.
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms, ...rest) => {
    const handle = realSetTimeout(fn, ms, ...rest);
    if (handle && typeof handle.unref === "function") handle.unref();
    return handle;
  };
  const restore = () => {
    globalThis.setTimeout = realSetTimeout;
    delete globalThis.document;
    delete globalThis.requestAnimationFrame;
  };
  // ASYNC-AWARE ON PURPOSE. A plain try/finally tore the globals down the moment `run()` returned its
  // PROMISE, so every await inside the upload ran with no `document` — which read as the function being
  // broken rather than the harness being wrong.
  let result;
  try {
    result = run();
  } catch (error) {
    restore();
    throw error;
  }
  if (result && typeof result.then === "function") {
    return result.then((v) => { restore(); return v; }, (e) => { restore(); throw e; });
  }
  restore();
  return result;
}

test("loadFiles stores the returned list and asks the shared endpoint", async () => {
  respond({ files: [{ name: "a.txt" }] });
  state.files = null;
  await loadFiles();
  assert.deepEqual(state.files, [{ name: "a.txt" }]);
  assert.equal(SEEN[0].url, "/api/v1/shared");
});

test("loadFiles KEEPS the previous list when the request fails", async () => {
  // `catch (_) { /* keep prior */ }`. Blanking the list on a transient error would make the page look
  // like every shared file had been deleted.
  respond({ detail: "boom" }, 500);
  state.files = [{ name: "previous.txt" }];
  await loadFiles();
  assert.deepEqual(state.files, [{ name: "previous.txt" }], "a failed refresh must not clear the list");
});

test("loadFiles accepts a bare array as well as {files}", async () => {
  // `res.files || res || []` — the endpoint has returned both shapes.
  respond([{ name: "bare.txt" }]);
  state.files = null;
  await loadFiles();
  assert.deepEqual(state.files, [{ name: "bare.txt" }]);
});

test("a filename with markup in it is ESCAPED into the list, not rendered", async () => {
  // The name comes from whoever uploaded the file. This is the one place it reaches innerHTML.
  const host = { innerHTML: "" };
  state.files = [{
    name: '<img src=x onerror="alert(1)">.txt', from: "agent-1", size: 12, ts: Date.now(),
  }];
  withDom({ "files-list": host }, renderFiles);
  assert.ok(!host.innerHTML.includes("<img src=x"), "the raw tag must not survive into the markup");
  assert.ok(host.innerHTML.includes("&lt;img"), "it must appear escaped instead");
});

test("renderFiles is a no-op when its host element is absent", () => {
  // It runs from the shared render path, not only on the files page.
  state.files = [{ name: "a.txt" }];
  withDom({}, renderFiles);
});

test("an empty list renders something rather than a blank panel", () => {
  const host = { innerHTML: "SENTINEL" };
  state.files = [];
  withDom({ "files-list": host }, renderFiles);
  assert.notEqual(host.innerHTML, "SENTINEL", "the host must be written to even with no files");
});

test("uploading sends multipart with a boundary — NOT application/json", async () => {
  // THE INVARIANT THE HEADER SPREAD EXISTS FOR. `headers: {}` clears api()'s JSON default so fetch can
  // generate the boundary. A merge instead of a replace would break every upload.
  respond({ ok: true, file: { name: "note.txt" } });
  const input = { files: [new File(["hello"], "note.txt", { type: "text/plain" })], value: "keep" };
  const nameField = { value: "" };
  const descField = { value: "a description" };
  state.settings = {};
  state.files = [];

  await withDom({
    "files-upload-input": input,
    "files-upload-name": nameField,
    "files-upload-desc": descField,
    "files-list": fakeNode(),
  }, () => uploadSharedFile());

  const post = SEEN.find((r) => r.method === "POST");
  assert.match(post.headers["content-type"], /^multipart\/form-data; boundary=/);
  assert.match(post.body, /note\.txt/, "the filename must reach the service");
  assert.match(post.body, /a description/, "the description travels with it");
  assert.equal(input.value, "", "the picker is cleared so the same file can be chosen again");
});

test("the file picker's own name is used unless the operator typed one", async () => {
  respond({ ok: true });
  const input = { files: [new File(["x"], "original.txt")], value: "" };
  state.settings = {};
  state.files = [];
  await withDom({
    "files-upload-input": input,
    "files-upload-name": { value: "  chosen.txt  " },
    "files-upload-desc": { value: "" },
    "files-list": fakeNode(),
  }, () => uploadSharedFile());
  const post = SEEN.find((r) => r.method === "POST");
  assert.match(post.body, /chosen\.txt/, "a typed name wins and is trimmed");
  assert.ok(!post.body.includes("original.txt"), "the picker's name should not also be sent");
});

test("uploading with NO file selected makes no request at all", async () => {
  // An empty multipart POST is a wasted round trip and the error it returns is not actionable.
  respond({ ok: true });
  await withDom({ "files-upload-input": { files: [] } }, () => uploadSharedFile());
  assert.deepEqual(SEEN, [], "nothing should have been sent");
});

test("a file over the configured size cap is refused BEFORE it is uploaded", async () => {
  // `max_shared_size_mb` is pre-checked so a large file is not pushed across the wire just to collect a
  // 413 from the service.
  respond({ ok: true });
  state.settings = { max_shared_size_mb: 1 };
  await withDom({ "files-upload-input": { files: [{ size: 5 * 1024 * 1024, name: "big.bin" }] } },
    () => uploadSharedFile());
  assert.deepEqual(SEEN, [], "over the cap: nothing sent");
});

// NOT COVERED: that a cap of 0 means "unset" rather than "reject everything".
//
// It is the inversion most worth pinning here, and I could not get it to run. The assertion needs a
// SECOND real upload in this file, and the fourth POST never returns — the test before it and the one
// before that each perform an upload and pass, and the same test passes on its own or paired with any
// single predecessor, so it is cumulative rather than caused by any one test. Unref'ing the server,
// unref'ing the toast timers, and closing keep-alive connections each failed to change it, and three
// failed fixes is where I stop guessing. Recorded rather than deleted, and rather than left as a
// mysteriously absent case: the guard reads `if (maxMb && ...)`, so a 0 short-circuits, but nothing here
// proves it.

test("a pasted image gets a stable, extension-correct name", () => {
  // Pasted blobs arrive with no filename. The name has to be unique enough not to collide with the last
  // paste and carry an extension the service will accept.
  const a = pastedImageName({ type: "image/png" });
  const b = pastedImageName({ type: "image/jpeg" });
  assert.match(a, /\.png$/);
  assert.match(b, /\.jpe?g$/);
  assert.ok(a.length > 4 && !a.startsWith("."), "the name must not be only an extension");
});

test("the module imports in Node with no browser globals present", async () => {
  // The whole point of the extraction: app.js cannot be imported, this can. `apiBase` arrives as a live
  // binding from api-client rather than being recomputed here, which is what keeps it importable.
  for (const g of ["document", "localStorage", "location"]) {
    assert.equal(g in globalThis, false, `${g} must not be needed to import this module`);
  }
  const again = await import("./shared-files.mjs");
  assert.equal(again.loadFiles, loadFiles, "one module instance, no load-time side effects");
});
