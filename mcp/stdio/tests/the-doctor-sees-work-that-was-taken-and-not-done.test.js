#!/usr/bin/env node
// A spawn request a host CLAIMED and never started must be visible.
//
// EXTERNAL REVIEW, Round 8 M9. The doctor read no spawn-request state at all -- zero hits across
// `doctor.js`, every `*-check.mjs` and every predicate. So a request taken by a host that then did
// nothing was invisible to every instrument, and the operator saw a spawn that was accepted and
// never appeared.
//
// AND IT IS WHY H2 WAS INVISIBLE, which is the argument for this check rather than a nicety. That
// defect stopped a host's claim loop dead on one failed report, while the SEPARATE heartbeat loop
// kept `bridgeLastSeen` fresh: `env-bridge` passed, `spawn-delegation` passed, `bridge-current`
// passed, and the queue never drained. Every row was green because every row asked a different
// question. This is the question nobody was asking.

import assert from "node:assert/strict";
import test from "node:test";

import {
  CLAIMED_GRACE_SECONDS,
  TAKEN_NOT_RUNNING,
  spawnQueueVerdict,
} from "../spawn-queue-check.mjs";

const NOW = Date.parse("2026-09-04T12:00:00.000Z");
const now = () => NOW;
const agoSeconds = (n) => new Date(NOW - n * 1000).toISOString();

const listing = (rows) => async () => ({ ok: true, spawnRequests: rows });

test("a request CLAIMED long ago and never started is named", async () => {
  const verdict = await spawnQueueVerdict({
    list: listing([{
      id: "sr-1", status: "claimed", environmentId: "windows:host:default",
      claimedAt: agoSeconds(CLAIMED_GRACE_SECONDS + 60),
    }]),
    now,
  });
  assert.equal(verdict.ok, false, "a spawn taken and not done reported as healthy");
  assert.equal(verdict.code, "stuck-claims");
  assert.match(verdict.detail, /sr-1/, "the row must be NAMED, or an operator cannot act on it");
  assert.match(verdict.detail, /windows:host:default/, "and the host it is stuck on");
});

test("a request claimed SECONDS ago is left alone", async () => {
  // THE CONTROL. Claiming and starting are two steps with a real gap between them; a check that
  // fired on every fresh claim would be red constantly and switched off, taking the signal with it.
  const verdict = await spawnQueueVerdict({
    list: listing([{ id: "sr-2", status: "claimed", claimedAt: agoSeconds(5) }]),
    now,
  });
  assert.equal(verdict.ok, true, `a spawn claimed 5s ago was reported as stuck: ${verdict.detail}`);
});

test("a QUEUED request is not this check's business, however old", async () => {
  // A request nobody has taken is a request waiting for a host, which is a different condition with
  // a different remedy -- and `env-bridge` already reports it. Counting it here would make this row
  // fire for a reason its own fix text does not address.
  const verdict = await spawnQueueVerdict({
    list: listing([{ id: "sr-3", status: "queued", createdAt: agoSeconds(9999) }]),
    now,
  });
  assert.equal(verdict.ok, true, "an unclaimed request was reported as taken-and-not-done");
});

test("EVERY taken-but-not-running status is covered, derived from the list", async () => {
  // `claimed` and `starting` both mean a host has the work and no process exists yet. Asserted over
  // the exported list rather than by naming one, so a third status added there is covered the day it
  // lands instead of the day somebody remembers this test.
  for (const status of TAKEN_NOT_RUNNING) {
    const verdict = await spawnQueueVerdict({
      list: listing([{ id: `sr-${status}`, status, claimedAt: agoSeconds(CLAIMED_GRACE_SECONDS + 60) }]),
      now,
    });
    assert.equal(verdict.ok, false, `a request stuck in \`${status}\` was reported as healthy`);
  }
});

test("a RUNNING request is not stuck, because the work is happening", async () => {
  const verdict = await spawnQueueVerdict({
    list: listing([{ id: "sr-4", status: "running", claimedAt: agoSeconds(99999) }]),
    now,
  });
  assert.equal(verdict.ok, true, "a running spawn was reported as stuck");
});

test("A LISTING THAT COULD NOT BE READ IS NOT A PASS", async () => {
  // This repo has fixed green-by-default twice -- `env-bridge` counting registered rows, and
  // `bridge-current` before `a2f9e42`. A check that gathered no evidence must not read as one that
  // gathered evidence and found nothing.
  const verdict = await spawnQueueVerdict({
    list: async () => { throw new Error("connection refused"); },
    now,
  });
  assert.equal(verdict.ok, false, "an unreadable queue reported as healthy");
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /connection refused/, "the real cause must survive into the report");
});

test("an EMPTY queue is honestly ok, and says which kind of ok it is", async () => {
  const verdict = await spawnQueueVerdict({ list: listing([]), now });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "empty",
    "'nothing to check' and 'checked and clean' are different answers; folding them together is how "
    + "a check stops being readable");
});

test("a claim with an UNREADABLE timestamp is skipped rather than guessed at", async () => {
  // Inventing an age would either invent a stuck row or hide one. The row is left for the checks
  // that read status rather than time.
  const verdict = await spawnQueueVerdict({
    list: listing([{ id: "sr-5", status: "claimed", claimedAt: "not a time" }]),
    now,
  });
  assert.equal(verdict.ok, true, "an unparseable claim time was treated as an old one");
});
