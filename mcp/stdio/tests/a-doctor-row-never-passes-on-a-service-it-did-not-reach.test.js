#!/usr/bin/env node
// Two rows printed green about a service they never reached.
//
// THE MECHANISM, read out of `doctor.js` rather than assumed: its `get()` catches everything and
// returns `null` — 401, 403, any non-2xx, any transport failure. It NEVER throws. So
// `spawnQueueVerdict`'s `catch` branch, which exists precisely to produce `unknown-all`, is
// unreachable from the only call site there is; and `tierVersionVerdict` had its null discarded by
// `(await get(...))?.environments || []` before it was ever asked the question.
//
// WHAT THEY PRINTED: `ok`, "no spawn requests on this service", and `ok`, "no live environment tier
// to compare against". Both are claims about a service nothing read, printed underneath a `service`
// row that is failing loudly. The condition is ordinary — an API key set with the doctor holding the
// wrong one is exactly what `doctor-api-key.mjs` was written for, and a stopped container does it too.
//
// This repo has now shipped this same false green three times: `env-bridge` counting registered rows,
// `bridge-current` green-by-default, and here. The correct pattern was already in the tree —
// `session-handle-check.mjs` asks whether the listing was readable before it counts anything.

import assert from "node:assert/strict";
import test from "node:test";

import { spawnQueueVerdict } from "../spawn-queue-check.mjs";
import { tierVersionVerdict } from "../tier-version-check.mjs";

test("spawn-queue: an UNREADABLE listing is not an empty queue", async () => {
  const verdict = await spawnQueueVerdict({ list: async () => null });
  assert.equal(verdict.ok, false, "a service that was never read reported a pass");
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /NOT/, "the row must say what it is not claiming");
});

test("spawn-queue: CONTROL — a genuinely empty queue still passes", async () => {
  // Without this the fix above could be "always fail", which detects everything and is useless.
  const verdict = await spawnQueueVerdict({ list: async () => ({ spawnRequests: [] }) });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "empty");
});

test("spawn-queue: a TRUNCATED page is not the whole queue", async () => {
  // The endpoint caps its listing and says so. A claim stuck behind a hundred newer requests is
  // outside the page, and reporting a window as the queue makes it invisible. `env-processes-check`
  // reads the same flag for the same reason, so the precedent existed.
  const verdict = await spawnQueueVerdict({ list: async () => ({ spawnRequests: [], truncated: true }) });
  assert.equal(verdict.code, "partial", "a window was reported as though it were the whole queue");
});

test("spawn-queue: a THROWING listing still reports unknown-all", async () => {
  // The branch that was unreachable from production. It stays, because `list` is injected and a
  // future caller may well throw.
  const verdict = await spawnQueueVerdict({ list: async () => { throw new Error("ECONNREFUSED"); } });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
});

test("tier-version: an UNREADABLE listing is not an empty fleet", async () => {
  const verdict = tierVersionVerdict({ environments: null });
  assert.equal(verdict.ok, false, "a service that was never read reported a pass");
  assert.equal(verdict.code, "unknown-all");
});

test("tier-version: CONTROL — a genuinely empty fleet is not a failure", async () => {
  const verdict = tierVersionVerdict({ environments: [] });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "none-live");
});

test("tier-version: an agreement reached by examining NO rows is not stated as one", async () => {
  // Every live row declaring a third `bridgeKind` is skipped, both accumulators stay empty, and the
  // final return announced "every live aify-env is 0.6.2 or newer" having judged nothing. No third
  // kind exists today — but this is the scoped-out-population shape this check's own header
  // describes, and its FIRST VERSION shipped exactly that: it scoped to rows that announced
  // themselves, which scoped OUT every row that was behind, and reported green on the operator's own
  // host while its tier was two versions old.
  //
  // NOT A FAILURE, THOUGH, and that distinction is the point. A row belonging to another tier is
  // deliberately not this check's business — `env-bridge` and `bridge-current` own it, and a second
  // row for one action teaches an operator to skim both. So the verdict is the one this file already
  // has for "there is no aify-env here", which claims nothing about versions.
  const verdict = tierVersionVerdict({
    environments: [{ id: "e1", metadata: { bridgeKind: "aify-dashboard" } }],
    isLive: () => true,
  });
  assert.equal(verdict.code, "none-live", "it claimed an agreement about a population it never read");
  assert.doesNotMatch(verdict.detail, /or newer/,
    "the detail asserted a version fact after examining no rows");
});

test("tier-version: CONTROL — a row it CAN judge is still judged", async () => {
  const verdict = tierVersionVerdict({
    environments: [{ id: "e1", bridgeVersion: "9.9.9", metadata: { bridgeKind: "aify-env" } }],
    isLive: () => true,
  });
  assert.equal(verdict.ok, true);
  assert.match(verdict.detail, /1 judged/, "the row should say how many it actually examined");
});
