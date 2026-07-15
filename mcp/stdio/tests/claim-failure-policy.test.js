import assert from "node:assert/strict";
import { test } from "node:test";
import { claimFailureDecision, claimRecoveryDecision } from "../claim-failure-policy.js";

test("transient claim failures are quiet until sustained, then rate-limited", () => {
  assert.deepEqual(claimFailureDecision({ count: 1, lastLogAt: 0, now: 1_000 }), {
    debug: true, warn: false, nextLastLogAt: 0,
  });
  assert.equal(claimFailureDecision({ count: 2, lastLogAt: 0, now: 2_000 }).warn, false);
  assert.equal(claimFailureDecision({ count: 3, lastLogAt: 0, now: 3_000 }).warn, true);
  assert.equal(claimFailureDecision({ count: 4, lastLogAt: 3_000, now: 4_000 }).warn, false);
  assert.equal(claimFailureDecision({ count: 5, lastLogAt: 3_000, now: 33_001 }).warn, true);
});

test("recovery is noteworthy only after the sustained-failure threshold", () => {
  assert.equal(claimRecoveryDecision(1).log, false);
  assert.equal(claimRecoveryDecision(2).log, false);
  assert.equal(claimRecoveryDecision(3).log, true);
});
