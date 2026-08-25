#!/usr/bin/env node
// Writing to and resizing a delegated process.
//
// aify-env exposes `POST /processes/:id/input` and `/resize`; EnvClient could not call either, so a
// delegated console could be watched and not typed at. v0.6 Phase 8 item 3b needs both before a `term`
// shim can stand in for a local pty.
//
// REPORTS, NEVER THROWS, like every other method here. "Unreachable" and "refused" are different
// answers and a caller falls back differently on each; an exception makes them identical to a catch
// block, which then does the wrong one.
//
// RESIZE HAS A TRAP AND THE ANSWER CARRIES IT. A piped process has no terminal to resize. aify-env
// says so rather than accepting silently, because a console that believes it set a width while the
// agent keeps wrapping at the default has no way to find out.

import assert from "node:assert/strict";
import { test } from "node:test";

import { EnvClient } from "../env-client.mjs";

/** Records what was asked, answers what it was told to. */
function fakeFetch(reply) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url: String(url), method: init?.method, body: init?.body ? JSON.parse(init.body) : null });
    if (reply instanceof Error) throw reply;
    return reply;
  };
  fn.calls = calls;
  return fn;
}

const ok = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => "" });
const client = (fetchImpl) => new EnvClient({ endpoint: "http://127.0.0.2:1", fetchImpl });

test("write posts the data to the process's input endpoint", async () => {
  // 204 is what aify-env answers for input and resize. This file stubbed 200 — the same wrong number
  // the client declared — so the unit test agreed with the client and both disagreed with the server.
  // Two copies of one assumption, neither checked against the producer.
  const f = fakeFetch(ok(204, null));
  const res = await client(f).write("p1", "hello\n");

  assert.equal(res.ok, true);
  assert.equal(f.calls[0].method, "POST");
  assert.match(f.calls[0].url, /\/processes\/p1\/input$/);
  assert.deepEqual(f.calls[0].body, { data: "hello\n" });
});

test("an id needing encoding is encoded, not concatenated", async () => {
  const f = fakeFetch(ok(204, null));
  await client(f).write("a/b c", "x");
  assert.match(f.calls[0].url, /\/processes\/a%2Fb%20c\/input$/);
});

test("resize sends the dimensions it was given", async () => {
  const f = fakeFetch(ok(204, null));
  const res = await client(f).resize("p1", 120, 40);

  assert.equal(res.ok, true);
  assert.match(f.calls[0].url, /\/processes\/p1\/resize$/);
  assert.deepEqual(f.calls[0].body, { cols: 120, rows: 40 });
});

test("a refusal is REPORTED, not thrown", async () => {
  // The piped-process case: aify-env answers that it did not apply.
  const f = fakeFetch(ok(400, { error: "process p1 has no terminal to resize" }));
  const res = await client(f).resize("p1", 120, 40);

  assert.equal(res.ok, false);
  assert.match(res.error, /no terminal/i);
});

test("an unreachable environment is reported too, and reads differently from a refusal", async () => {
  const res = await client(fakeFetch(new Error("ECONNREFUSED"))).write("p1", "x");
  assert.equal(res.ok, false);
  assert.ok(res.error, "an unreachable environment must say so");
});

test("no endpoint configured is refused rather than fetched", async () => {
  const f = fakeFetch(ok(200, { ok: true }));
  const res = await new EnvClient({ endpoint: "", fetchImpl: f }).write("p1", "x");
  assert.equal(res.ok, false);
  assert.equal(f.calls.length, 0, "it tried to fetch with no endpoint");
});
