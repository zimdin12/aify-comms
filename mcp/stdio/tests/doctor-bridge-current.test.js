#!/usr/bin/env node
// Does a LIVE bridge run current code? Nothing on Windows answered that until now.
//
// v0.2 item B1, promoted after it cost a real hour. `bridge-installed` proves the FILES on disk
// match the checkout — a different claim from what any PROCESS loaded at boot. `bridge-running`
// exists to close that gap but reads /proc and SKIPS on Windows, so on this host the question was
// simply unanswered.
//
// THE LIVE ARTIFACT: on 2026-08-10 a multipart fix was verified through comms_share, returned the
// OLD corrupted bytes, and was nearly recorded as a broken fix. The fix was correct; the bridge
// making the call was pre-restart, and no check said so.
//
// The bridge already computed its build sha for the startup banner and wrote it only to stderr.
// It now reports it on registration, so this check needs no process inspection at all — which is
// what makes it work on every platform.

import assert from "node:assert/strict";
import { test } from "node:test";
import { bridgeCurrentVerdict } from "../doctor-predicates.js";

const HEAD = "4157299abcdef0123456789abcdef0123456789a";
const SHORT = "4157299";
const now = new Date().toISOString();

const env = (id, build, extra = {}) => ({
  id,
  status: "online",
  lastSeen: now,
  metadata: build === undefined ? {} : { bridgeBuild: build },
  ...extra,
});

// ── the failure it exists to catch ───────────────────────────────────────────────────
test("a live bridge running older code is reported STALE", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("win:host", "deadbeef1234")],
    headSha: HEAD,
    headShort: SHORT,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale-process");
  assert.match(v.detail, /RUNNING older code/);
  assert.match(v.detail, /win:host running deadbee/);
});

test("the fix says RESTART, and explicitly not reinstall", () => {
  // Getting this wrong would send the operator to install.sh, which changes nothing when the
  // files are already current — the code is on disk, just not in memory.
  const v = bridgeCurrentVerdict({
    environments: [env("win:host", "deadbeef1234")], headSha: HEAD, headShort: SHORT,
  });
  assert.match(v.fix, /RESTART/);
  assert.match(v.fix, /will not help/i);
});

test("a live bridge on HEAD is green", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("win:host", HEAD.slice(0, 12))], headSha: HEAD, headShort: SHORT,
  });
  assert.equal(v.ok, true);
  assert.equal(v.code, "ok");
});

test("comparison tolerates the bridge's 12-char prefix vs a full sha", () => {
  // The bridge reports 12 chars; repo.sha is 40. A naive === would flag every healthy bridge.
  const v = bridgeCurrentVerdict({
    environments: [env("a", HEAD.slice(0, 12))], headSha: HEAD, headShort: SHORT,
  });
  assert.equal(v.ok, true, "a 12-char prefix of HEAD must count as current");
});

// ── must not cry wolf ────────────────────────────────────────────────────────────────
test("OFFLINE bridges are ignored — a dead bridge's build claims nothing", () => {
  const dead = { id: "old", status: "offline", lastSeen: "2020-01-01T00:00:00Z", metadata: { bridgeBuild: "deadbeef1234" } };
  const v = bridgeCurrentVerdict({ environments: [dead], headSha: HEAD, headShort: SHORT });
  assert.equal(v.ok, true);
  assert.equal(v.code, "skipped");
});

test("a pre-B1 bridge that reports no build is PARTIAL, not a failure", () => {
  // Older bridges simply do not send the field yet. Failing on that would make the check red for
  // everyone until every wrapper restarts — alarm fatigue on its first day.
  const v = bridgeCurrentVerdict({
    environments: [env("a", HEAD.slice(0, 12)), env("b", undefined)], headSha: HEAD, headShort: SHORT,
  });
  assert.equal(v.ok, true);
  assert.equal(v.code, "partial");
  assert.match(v.detail, /did not report a build sha/);
});

test("sentinel build values count as unknown, not stale", () => {
  for (const sentinel of ["unknown", "no-git", "unknown-ref", ""]) {
    const v = bridgeCurrentVerdict({
      environments: [env("a", sentinel)], headSha: HEAD, headShort: SHORT,
    });
    assert.equal(v.ok, true, `"${sentinel}" must not be read as a mismatched sha`);
  }
});

test("no checkout, or no live bridge, skips rather than guessing", () => {
  assert.equal(bridgeCurrentVerdict({ environments: [env("a", "x")], headSha: "" }).code, "skipped");
  assert.equal(bridgeCurrentVerdict({ environments: [], headSha: HEAD }).code, "skipped");
  assert.equal(bridgeCurrentVerdict({}).code, "skipped");
});

test("one stale among several current is still reported", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("good", HEAD.slice(0, 12)), env("bad", "0000deadbeef")],
    headSha: HEAD,
    headShort: SHORT,
  });
  assert.equal(v.ok, false);
  assert.match(v.detail, /1 live bridge\(s\)/);
  assert.match(v.detail, /bad running 0000dea/);
});

console.log("doctor-bridge-current.test.js: all assertions passed");
