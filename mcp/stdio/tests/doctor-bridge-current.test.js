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

// A LIVE BRIDGE NOW MEANS A RECENT `bridgeLastSeen`, not `status: "online"`. Since aify-env began
// advertising, `status` says that tier DESCRIBES the host -- a row can be online for a day with no
// bridge on it, which is exactly what happened on 2026-09-02 and what `bridgeCurrentVerdict` must
// stop reading as a running process. These fixtures model a real live bridge; without the stamp they
// model an advertised host, and every assertion below would pass by being skipped.
const env = (id, build, extra = {}) => ({
  id,
  status: "online",
  lastSeen: now,
  metadata: {
    bridgeLastSeen: now,
    ...(build === undefined ? {} : { bridgeBuild: build }),
  },
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
  // Paired with a known-current bridge so the fleet is PARTIAL: this test is about the sentinel
  // not being misread as a mismatched sha, which is a different question from the all-silent
  // fleet below.
  for (const sentinel of ["unknown", "no-git", "unknown-ref", ""]) {
    const v = bridgeCurrentVerdict({
      environments: [env("cur", HEAD.slice(0, 12)), env("a", sentinel)], headSha: HEAD, headShort: SHORT,
    });
    assert.equal(v.code, "partial", `"${sentinel}" must not be read as a mismatched sha`);
    assert.equal(v.ok, true);
  }
});

// ── AUDIT 4/4 F1: the check must not pass on no evidence ─────────────────────────────
//
// LIVE-CONFIRMED on this host at v0.3.1: the single online environment bridge reported no
// `bridgeBuild`, this returned ok, and `aify-doctor --strict` PASSED — having verified nothing.
// That is the same false green as `env-bridge` counting registered rows (756f3a5), reproduced
// inside the check written to prevent exactly this class.
//
// The distinction that fixes it: SOME evidence with gaps is a partial pass; ZERO evidence is not a
// pass at all. A check that cannot answer must not be counted as one that answered yes.
test("when NO live bridge reports a build, the check FAILS — no evidence is not a pass", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("a", undefined), env("b", "unknown")], headSha: HEAD, headShort: SHORT,
  });
  assert.equal(v.ok, false, "a check that verified nothing must not read as verified");
  assert.equal(v.code, "unknown-all");
  assert.match(v.detail, /nothing here verifies them/);
  assert.match(v.detail, /no evidence either way/);
});

test("unknown-all says restart, and says install.sh alone is not it", () => {
  const v = bridgeCurrentVerdict({ environments: [env("a", undefined)], headSha: HEAD, headShort: SHORT });
  assert.equal(v.code, "unknown-all");
  assert.match(v.fix, /Restart/);
  assert.match(v.fix, /install\.sh alone will not/);
});

test("one current bridge is enough to make the rest PARTIAL rather than unknown-all", () => {
  // The boundary between the two verdicts, pinned: the difference is whether ANY live bridge
  // produced evidence, not how many did.
  const v = bridgeCurrentVerdict({
    environments: [env("cur", HEAD.slice(0, 12)), env("a", undefined), env("b", undefined)],
    headSha: HEAD,
    headShort: SHORT,
  });
  assert.equal(v.code, "partial");
  assert.equal(v.ok, true);
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


// ── the cry-wolf case: behind HEAD, but not by anything the bridge RUNS ───────────────
//
// Observed 2026-08-11, minutes after shipping the check: HEAD moved by four commits — CLAUDE.md,
// BRIDGE_SETUP.md, install.sh, a test file — and this reported the live bridge as "RUNNING older
// code", telling the operator to restart it. Its bridge code was byte-identical. `bridge-installed`
// had already solved exactly this for the files on disk (N13) by asking whether the commits in
// between TOUCHED mcp/stdio; this now asks the same question of the running process.
test("a bridge behind HEAD by NON-bridge commits only is not stale", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("win:host", "0b2801daaaa")],
    headSha: HEAD,
    headShort: SHORT,
    bridgeCommitsSince: { "0b2801daaaa": 0 },
  });
  assert.equal(v.ok, true);
  assert.equal(v.code, "ok-nonbridge");
  assert.match(v.detail, /no commit in between touched/);
  assert.equal(v.fix, "", "there is nothing to do, so do not ask for a restart");
});

test("one commit that DOES touch the bridge still reports stale", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("win:host", "0b2801daaaa")],
    headSha: HEAD,
    headShort: SHORT,
    bridgeCommitsSince: { "0b2801daaaa": 1 },
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale-process");
});

test("a build with NO count is stale, not clean — unanswerable is not evidence", () => {
  // git can fail to resolve a build sha (rewritten history, unfetched commit). Falling back to
  // "clean" there would be the unknown-all false green again, one layer down.
  const v = bridgeCurrentVerdict({
    environments: [env("win:host", "deadbeef1234")],
    headSha: HEAD,
    headShort: SHORT,
    bridgeCommitsSince: {},
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale-process");
});

test("stale wins over behind-by-docs when both are present", () => {
  const v = bridgeCurrentVerdict({
    environments: [env("docsonly", "0b2801daaaa"), env("real", "cafebabe0000")],
    headSha: HEAD,
    headShort: SHORT,
    bridgeCommitsSince: { "0b2801daaaa": 0, cafebabe0000: 3 },
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale-process");
  assert.match(v.detail, /real running cafebab/);
  assert.doesNotMatch(v.detail, /docsonly/, "the clean one must not be named as needing a restart");
});

console.log("doctor-bridge-current.test.js: all assertions passed");
