#!/usr/bin/env node
// A `term` that is not a pty: the shim standing in for one when a process lives in aify-env.
//
// v0.6 Phase 8 item 3b. TerminalProcessManager reaches for `state.term` to write, resize and kill, and
// the console keepalive probes it. A delegated process has no local pty, so the seam refuses today
// rather than half-delegating. This is the object that makes the refusal unnecessary.
//
// THE HARD PART IS THAT THE CALLS ARE SYNCHRONOUS. Every call site does `terminal.term.write(...)`
// with no await, because a pty's write returns nothing. EnvClient is HTTP and async. So the shim
// dispatches without blocking -- and a failure that nobody awaited must not vanish, or typing into a
// delegated console would silently do nothing. It reports through onError instead.
//
// THE SURFACE IS DERIVED FROM THE MANAGER, not from this file's idea of it. If terminal-runtime.js
// starts calling a fifth member, the last test here fails rather than the shim throwing in production.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createEnvTerm } from "../env-term-shim.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** An EnvClient stand-in that records calls and answers what it is told to. */
function fakeClient(answer = { ok: true }) {
  const calls = [];
  return {
    calls,
    async write(id, data) { calls.push(["write", id, data]); return answer; },
    async resize(id, cols, rows) { calls.push(["resize", id, cols, rows]); return answer; },
    async stop(id) { calls.push(["stop", id]); return answer; },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("write forwards to the client without the caller awaiting", async () => {
  const client = fakeClient();
  const term = createEnvTerm({ client, id: "p1", pid: 4242 });

  const returned = term.write("hello\n");
  assert.equal(returned, undefined, "a pty write returns nothing; the shim must match");
  await settle();
  assert.deepEqual(client.calls[0], ["write", "p1", "hello\n"]);
});

test("resize and kill forward too, and kill stops the process", async () => {
  const client = fakeClient();
  const term = createEnvTerm({ client, id: "p1", pid: 1 });
  term.resize(120, 40);
  term.kill();
  await settle();
  assert.deepEqual(client.calls, [["resize", "p1", 120, 40], ["stop", "p1"]]);
});

test("pid is the one the environment reported", () => {
  assert.equal(createEnvTerm({ client: fakeClient(), id: "p1", pid: 4242 }).pid, 4242);
});

test("a REFUSED call is reported, because nobody awaited it", async () => {
  // The failure this exists to prevent: typing into a delegated console and nothing happening, with
  // no error anywhere because the promise nobody held resolved to {ok:false}.
  const seen = [];
  const client = fakeClient({ ok: false, error: "no such process" });
  const term = createEnvTerm({ client, id: "p1", pid: 1, onError: (op, err) => seen.push([op, err]) });

  term.write("x");
  await settle();
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], "write");
  assert.match(seen[0][1], /no such process/);
});

test("a THROWN call is reported rather than becoming an unhandled rejection", async () => {
  const seen = [];
  const client = { async write() { throw new Error("ECONNREFUSED"); } };
  const term = createEnvTerm({ client, id: "p1", pid: 1, onError: (op, err) => seen.push([op, err]) });

  term.write("x");
  await settle();
  assert.equal(seen[0][0], "write");
  assert.match(seen[0][1], /ECONNREFUSED/);
});

test("a missing onError does not turn a failure into a crash", async () => {
  // The shim must be safe to construct without one; losing the report is bad, crashing the bridge is
  // worse, and an unhandled rejection takes the process down.
  const term = createEnvTerm({ client: fakeClient({ ok: false, error: "nope" }), id: "p1", pid: 1 });
  term.write("x");
  await settle();
});

test("the shim covers every member terminal-runtime.js reaches for", () => {
  // DERIVED, so a fifth call site fails here instead of throwing in production.
  const source = fs.readFileSync(path.join(HERE, "..", "terminal-runtime.js"), "utf8");
  const used = new Set(
    [...source.matchAll(/\.term\??\.([a-zA-Z]+)/g)].map((m) => m[1]),
  );
  assert.ok(used.size >= 4, `only found ${used.size} term members; the scan probably broke`);

  const term = createEnvTerm({ client: fakeClient(), id: "p1", pid: 1 });
  const missing = [...used].filter((name) => term[name] === undefined).sort();
  assert.deepEqual(
    missing,
    [],
    `terminal-runtime.js uses term members the shim does not provide: ${missing.join(", ")}`,
  );
});
