// Swapping the live handlers on a POOLED JSON-RPC client, mid-life.
//
// Twenty-first cluster off the V8-coverage census: `runtimes-rpc.js`'s `setOnStderr` and `setOnRequest`. Their
// sibling `setOnNotification` is covered; these two had a zero call count.
//
// WHY THEY EXIST AT ALL, from the code's own note: a pooled RPC (CodexSession) swaps its handlers PER TURN
// without rebuilding the client. So the swap is not a convenience — it is how each new turn takes ownership of
// the stream it is supposed to be reading.
//
// AND `setOnRequest` GOVERNS APPROVALS. A codex app-server asks the client for command-execution and
// file-change approvals as JSON-RPC REQUESTS, which means it waits for an answer. If a swap did not take, the
// approval would be answered by the previous turn's handler — or by nothing at all, and codex sits blocked on a
// reply that never comes, with no error anywhere.
//
// A FAKE PROC of PassThrough streams, not a child process: `createRpcClient` takes the process object, so the
// whole protocol is exercisable in memory. Nothing spawns codex.
//
// ONE MUTATION SURVIVES: dropping the `typeof handler === "function"` guard from `setOnRequest` (its twin in
// `setOnStderr` is caught, because a stored non-function is CALLED there). For requests the guard is redundant
// with `answerServerRequest`'s own type check, which turns any non-function into the same -32601 error reply.
// Both readings therefore produce identical frames on the wire, and no assertion through this surface can tell
// them apart. Another guard behind a guard, like the codex target-home exclusion.

import assert from "node:assert/strict";
import test from "node:test";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import { createRpcClient } from "../runtimes-rpc.js";

// Emulates the shape createRpcClient consumes: `.on`, `.stdout`, `.stderr`, `.stdin`. Whatever the client writes
// to stdin is collected as parsed frames, which is how the replies get asserted.
function fakeProc() {
  const proc = new EventEmitter();
  proc.stdout = new PassThrough();
  proc.stderr = new PassThrough();
  const written = [];
  proc.stdin = new PassThrough();
  proc.stdin.on("data", (chunk) => {
    for (const line of String(chunk).split("\n")) {
      const text = line.trim();
      if (text) written.push(JSON.parse(text));
    }
  });
  proc.written = written;
  proc.emitStdout = (obj) => proc.stdout.write(`${typeof obj === "string" ? obj : JSON.stringify(obj)}\n`);
  proc.emitStderr = (line) => proc.stderr.write(`${line}\n`);
  return proc;
}

// readline delivers lines asynchronously; give it a turn of the loop.
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

// ── stderr ──────────────────────────────────────────────────────────────────

test("the stderr handler can be replaced, and the old one stops receiving", async () => {
  const first = [];
  const second = [];
  const proc = fakeProc();
  const rpc = createRpcClient(proc, { onStderr: (line) => first.push(line) });

  proc.emitStderr("before the swap");
  await settle();
  rpc.setOnStderr((line) => second.push(line));
  proc.emitStderr("after the swap");
  await settle();

  assert.deepEqual(first, ["before the swap"], "the replaced handler kept receiving lines");
  assert.deepEqual(second, ["after the swap"]);
});

test("clearing the stderr handler drops lines instead of throwing", async () => {
  // A turn that ends unsubscribes. The runtime keeps talking, and those lines must go nowhere quietly — a throw
  // inside a readline listener has no caller to catch it.
  const proc = fakeProc();
  const rpc = createRpcClient(proc, { onStderr: () => { throw new Error("must not be called"); } });

  for (const notAFunction of [null, undefined, "nope", 42, {}]) {
    rpc.setOnStderr(notAFunction);
    proc.emitStderr(`line for ${String(notAFunction)}`);
    await settle();
  }
  // Reaching here at all is the assertion: no listener ran and nothing threw.
  assert.ok(true);
});

test("a process error reaches the CURRENT stderr handler", async () => {
  // The spawn failure path routes through the same handler, so a swapped-in turn learns that its runtime died
  // rather than the constructor-time handler learning it on the turn's behalf.
  const seen = [];
  const proc = fakeProc();
  const rpc = createRpcClient(proc, { onStderr: () => { throw new Error("stale handler ran"); } });
  rpc.setOnStderr((line) => seen.push(line));

  proc.emit("error", new Error("spawn ENOENT"));
  await settle();
  assert.deepEqual(seen, ["spawn ENOENT"]);
});

// ── server requests (approvals) ──────────────────────────────────────────────

test("the server-request handler can be replaced, and the reply carries the request's id", async () => {
  // The reply id is what the app-server matches; a reply without it is an unanswered approval.
  const proc = fakeProc();
  const rpc = createRpcClient(proc, {
    onRequest: () => { throw new Error("the constructor-time handler answered after a swap"); },
  });

  rpc.setOnRequest(async (message) => ({ decision: "acceptForSession", saw: message.method }));
  proc.emitStdout({ jsonrpc: "2.0", id: 77, method: "item/commandExecution/requestApproval", params: {} });
  await settle();

  assert.equal(proc.written.length, 1, "the approval request went unanswered");
  assert.deepEqual(proc.written[0], {
    jsonrpc: "2.0",
    id: 77,
    result: { decision: "acceptForSession", saw: "item/commandExecution/requestApproval" },
  });
});

test("clearing the request handler still ANSWERS, with an error", async () => {
  // The dangerous alternative is silence: codex blocks forever on an approval nobody replied to, and the agent
  // looks idle. An error reply at least ends the wait and names the method that could not be handled.
  const proc = fakeProc();
  const rpc = createRpcClient(proc, { onRequest: async () => ({ decision: "acceptForSession" }) });
  rpc.setOnRequest(null);

  proc.emitStdout({ jsonrpc: "2.0", id: 5, method: "item/fileChange/requestApproval", params: {} });
  await settle();

  assert.equal(proc.written.length, 1, "no reply was sent for an unhandled request");
  assert.equal(proc.written[0].id, 5);
  assert.equal(proc.written[0].error?.code, -32601);
  assert.match(proc.written[0].error?.message, /item\/fileChange\/requestApproval/,
    "the error does not name the method it could not handle");
});

test("a handler that throws becomes an error reply, not an unhandled rejection", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc, {});
  rpc.setOnRequest(async () => { throw new Error("approval policy refused"); });

  proc.emitStdout({ jsonrpc: "2.0", id: 9, method: "item/permissions/requestApproval", params: {} });
  await settle();

  assert.equal(proc.written[0].id, 9);
  assert.match(proc.written[0].error?.message, /approval policy refused/);
});

test("a swapped-in handler receives the params, not just the method", async () => {
  // The permissions approval answers WITH the requested permissions; a handler that cannot see them cannot
  // echo them back, and codex treats the mismatch as a refusal.
  const seen = [];
  const proc = fakeProc();
  const rpc = createRpcClient(proc, {});
  rpc.setOnRequest(async (message) => {
    seen.push(message.params);
    return { permissions: message.params.permissions, scope: "session" };
  });

  proc.emitStdout({
    jsonrpc: "2.0",
    id: 11,
    method: "item/permissions/requestApproval",
    params: { permissions: { write: true } },
  });
  await settle();

  assert.deepEqual(seen, [{ permissions: { write: true } }]);
  assert.deepEqual(proc.written[0].result, { permissions: { write: true }, scope: "session" });
});

test("an id-less notification is NOT treated as a request needing a reply", async () => {
  // The discriminator is `hasOwnProperty("id") && method`. Answering a notification would put a frame on the
  // wire the app-server never asked for, and swallowing a real request would hang the turn.
  const requests = [];
  const notifications = [];
  const proc = fakeProc();
  const rpc = createRpcClient(proc, {});
  rpc.setOnRequest(async (message) => { requests.push(message.method); return {}; });
  rpc.setOnNotification((message) => { notifications.push(message.method); });

  proc.emitStdout({ jsonrpc: "2.0", method: "item/started", params: {} });
  await settle();

  assert.deepEqual(requests, [], "a notification was answered as a request");
  assert.deepEqual(notifications, ["item/started"]);
  assert.deepEqual(proc.written, [], "a reply was sent for a notification");
});

test("a reply to one of OUR requests is not mistaken for a server request", async () => {
  // Same discriminator from the other side: a response has an id and NO method. Routing it to the request
  // handler would leave our own call pending forever while we answered our own reply.
  const requests = [];
  const proc = fakeProc();
  const rpc = createRpcClient(proc, {});
  rpc.setOnRequest(async () => { requests.push("called"); return {}; });

  const pending = rpc.request("thread/start", { cwd: "." }, 5000);
  await settle();
  const sent = proc.written.find((frame) => frame.method === "thread/start");
  assert.ok(sent, "the request never reached the wire");

  proc.emitStdout({ jsonrpc: "2.0", id: sent.id, result: { threadId: "th_1" } });
  assert.deepEqual(await pending, { threadId: "th_1" });
  assert.deepEqual(requests, [], "our own reply was routed to the server-request handler");
});
