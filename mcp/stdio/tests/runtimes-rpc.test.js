// The JSON-RPC client the codex app-server integration runs on.
//
// THIRD BACKLOG PAYMENT, and the largest module on that list at 326 lines. Every managed codex turn goes
// through this: requests are correlated by id, notifications drive the console, and SERVER-initiated
// requests — approvals — are answered from here.
//
// `createRpcClient(proc, …)` takes its process as an argument, so the whole stdio client can be driven
// with a fake: an EventEmitter with two readable streams and a writable stdin that records what was sent.
// No spawn, no network. The WebSocket client and `codexAppServerReachable` are NOT covered — they need a
// real socket, and `codexAppServerReachable` is already exercised against a closed port by
// `resident-binding-health.test.js`.
//
// `managedCodexServerRequest` IS THE AUTO-APPROVAL, and it is the reason this file was worth writing
// first among the remaining backlog. It answers codex's approval requests without asking anyone. That is
// deliberate for an unattended managed run, and it is also the behaviour recorded as an open operator
// concern — so it is pinned here explicitly rather than left as four lines nobody reads.

import assert from "node:assert/strict";
import test from "node:test";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import {
  createRpcClient,
  managedCodexServerRequest,
  quoteForDisplay,
} from "../runtimes-rpc.js";

/** A stand-in for a spawned process: two readable streams in, one writable captured. */
function fakeProc() {
  const proc = new EventEmitter();
  proc.stdout = new PassThrough();
  proc.stderr = new PassThrough();
  proc.sent = [];
  proc.stdin = {
    write: (chunk) => { proc.sent.push(JSON.parse(String(chunk).trim())); return true; },
    end: () => {},
  };
  proc.emitLine = (obj) => proc.stdout.write(`${typeof obj === "string" ? obj : JSON.stringify(obj)}\n`);
  proc.emitStderr = (line) => proc.stderr.write(`${line}\n`);
  return proc;
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

// --- the auto-approval -----------------------------------------------------

test("AUTO-APPROVAL: a command-execution approval is accepted FOR THE SESSION, not once", () => {
  // `acceptForSession` means the next command in the same session is not asked about either. That is the
  // whole behaviour behind "managed codex auto-approves all approvals" — recorded, not hidden.
  assert.deepEqual(
    managedCodexServerRequest({ method: "item/commandExecution/requestApproval" }),
    { decision: "acceptForSession" },
  );
});

test("AUTO-APPROVAL: a file-change approval is accepted the same way", () => {
  assert.deepEqual(
    managedCodexServerRequest({ method: "item/fileChange/requestApproval" }),
    { decision: "acceptForSession" },
  );
});

test("a permissions request is granted EXACTLY what was asked for, scoped to the session", () => {
  // It echoes back the requested permission set rather than granting a fixed one, so the answer cannot be
  // broader than the request. Worth pinning: substituting a constant here would widen every grant.
  const requested = { fileWrite: true, network: false };
  assert.deepEqual(
    managedCodexServerRequest({ method: "item/permissions/requestApproval", params: { permissions: requested } }),
    { permissions: requested, scope: "session" },
  );
});

test("a permissions request with no permissions field grants an EMPTY set, not undefined", () => {
  const answer = managedCodexServerRequest({ method: "item/permissions/requestApproval" });
  assert.deepEqual(answer, { permissions: {}, scope: "session" });
});

test("an UNKNOWN server request throws rather than being approved by default", () => {
  // The safety property. A future codex request type must fail loudly instead of falling through to an
  // accept — silence here would auto-approve something nobody has reviewed.
  assert.throws(() => managedCodexServerRequest({ method: "item/somethingNew/requestApproval" }),
    /unsupported Codex server request: item\/somethingNew\/requestApproval/);
  assert.throws(() => managedCodexServerRequest({}), /\(missing method\)/);
  assert.throws(() => managedCodexServerRequest(undefined), /\(missing method\)/);
});

// --- request correlation ---------------------------------------------------

test("a request is sent as JSON-RPC and resolves with the matching response", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  const promise = rpc.request("session/new", { model: "gpt-5" });
  await tick();

  const sent = proc.sent[0];
  assert.equal(sent.jsonrpc, "2.0");
  assert.equal(sent.method, "session/new");
  assert.deepEqual(sent.params, { model: "gpt-5" });

  proc.emitLine({ jsonrpc: "2.0", id: sent.id, result: { sessionId: "s-1" } });
  assert.deepEqual(await promise, { sessionId: "s-1" });
  rpc.close();
});

test("responses are matched BY ID — an out-of-order reply resolves the right caller", async () => {
  // Two turns in flight is normal. Resolving by arrival order instead of id would hand one turn's answer
  // to the other, which is the kind of bug that looks like a model producing nonsense.
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  const first = rpc.request("a");
  const second = rpc.request("b");
  await tick();
  const [sentA, sentB] = proc.sent;

  proc.emitLine({ jsonrpc: "2.0", id: sentB.id, result: "B" });
  proc.emitLine({ jsonrpc: "2.0", id: sentA.id, result: "A" });

  assert.equal(await first, "A");
  assert.equal(await second, "B");
  rpc.close();
});

test("an error response REJECTS with the server's message", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  const promise = rpc.request("boom");
  await tick();
  proc.emitLine({ jsonrpc: "2.0", id: proc.sent[0].id, error: { code: -1, message: "no such session" } });
  await assert.rejects(promise, /no such session/);
  rpc.close();
});

test("a response for an unknown id is ignored rather than throwing", async () => {
  // Late replies arrive after a timeout has already removed the pending entry.
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  proc.emitLine({ jsonrpc: "2.0", id: 9999, result: "orphan" });
  await tick();
  rpc.close();
});

test("a malformed line is skipped, and the client keeps working", async () => {
  // The app-server interleaves its own output. One unparseable line must not kill the transport.
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  proc.emitLine("not json at all");
  proc.emitLine("");
  const promise = rpc.request("still/alive");
  await tick();
  proc.emitLine({ jsonrpc: "2.0", id: proc.sent[0].id, result: "yes" });
  assert.equal(await promise, "yes");
  rpc.close();
});

// --- notifications and server requests -------------------------------------

test("a message with a method and NO id is a notification", async () => {
  const proc = fakeProc();
  const seen = [];
  const rpc = createRpcClient(proc, { onNotification: (m) => seen.push(m) });
  proc.emitLine({ jsonrpc: "2.0", method: "item/started", params: { id: 1 } });
  await tick();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, "item/started");
  rpc.close();
});

test("a message with BOTH an id and a method is a server REQUEST and gets answered", async () => {
  // The distinction is the whole routing rule: id+method = request needing a reply, id alone = response,
  // method alone = notification. Treating a request as a notification leaves codex waiting forever.
  const proc = fakeProc();
  const rpc = createRpcClient(proc, { onRequest: managedCodexServerRequest });
  proc.emitLine({ jsonrpc: "2.0", id: 77, method: "item/commandExecution/requestApproval" });
  await tick();

  const reply = proc.sent.find((m) => m.id === 77);
  assert.ok(reply, "the server request must be answered");
  assert.deepEqual(reply.result, { decision: "acceptForSession" });
  rpc.close();
});

test("a server request with NO handler is answered with an error, not left hanging", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  proc.emitLine({ jsonrpc: "2.0", id: 5, method: "item/fileChange/requestApproval" });
  await tick();
  const reply = proc.sent.find((m) => m.id === 5);
  assert.ok(reply, "an unanswered request would stall the turn");
  assert.equal(reply.error.code, -32601);
  rpc.close();
});

test("a handler that throws produces an error reply rather than an unhandled rejection", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc, { onRequest: () => { throw new Error("nope"); } });
  proc.emitLine({ jsonrpc: "2.0", id: 6, method: "anything" });
  await tick();
  assert.match(proc.sent.find((m) => m.id === 6).error.message, /nope/);
  rpc.close();
});

test("the notification handler can be SWAPPED per turn, and null disables it", async () => {
  // A pooled session reuses one client across turns and re-points the handler each time; rebuilding the
  // client per turn would drop the connection.
  const proc = fakeProc();
  const first = [];
  const second = [];
  const rpc = createRpcClient(proc, { onNotification: (m) => first.push(m) });

  rpc.setOnNotification((m) => second.push(m));
  proc.emitLine({ jsonrpc: "2.0", method: "one" });
  await tick();
  assert.equal(first.length, 0, "the original handler must be replaced, not added to");
  assert.equal(second.length, 1);

  rpc.setOnNotification(null);
  proc.emitLine({ jsonrpc: "2.0", method: "two" });
  await tick();
  assert.equal(second.length, 1, "null disables forwarding");
  rpc.close();
});

test("stderr lines reach the stderr handler", async () => {
  const proc = fakeProc();
  const lines = [];
  const rpc = createRpcClient(proc, { onStderr: (l) => lines.push(l) });
  proc.emitStderr("codex: warning about something");
  await tick();
  assert.deepEqual(lines, ["codex: warning about something"]);
  rpc.close();
});

// --- failure modes ---------------------------------------------------------

test("a process error REJECTS every in-flight request, not just the next one", async () => {
  // The app-server dying mid-turn must not leave promises pending forever — that is a hung managed run
  // with nothing in the log.
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  const a = rpc.request("a");
  const b = rpc.request("b");
  await tick();
  proc.emit("error", new Error("spawn ENOENT"));
  await assert.rejects(a, /spawn ENOENT/);
  await assert.rejects(b, /spawn ENOENT/);
  rpc.close();
});

test("after a process error, a NEW request rejects immediately instead of waiting for a timeout", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  proc.emit("error", new Error("gone"));
  await assert.rejects(rpc.request("later"), /gone/);
  rpc.close();
});

test("close() rejects anything still pending", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  const promise = rpc.request("never/answered");
  await tick();
  rpc.close();
  await assert.rejects(promise, /rpc client closed/);
});

test("a request times out with a message naming the method and the budget", async () => {
  const proc = fakeProc();
  const rpc = createRpcClient(proc);
  await assert.rejects(rpc.request("slow/call", {}, 10), /slow\/call timed out after 10ms/);
  rpc.close();
});

// --- display ---------------------------------------------------------------

test("quoteForDisplay collapses all whitespace to single spaces and trims", () => {
  // It renders arbitrary model output into one log line; an embedded newline would break the line format.
  assert.equal(quoteForDisplay("  a\n\tb   c  "), "a b c");
  assert.equal(quoteForDisplay(""), "");
  assert.equal(quoteForDisplay(undefined), "", "absent text is empty, not 'undefined'");
  assert.equal(quoteForDisplay(42), "42");
});
