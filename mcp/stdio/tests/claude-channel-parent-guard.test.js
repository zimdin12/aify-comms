#!/usr/bin/env node
// B3 (visible-TUI): orphan-sidecar self-exit guard. Pure-predicate tests only —
// no real pids, no spawning, no killing. Verifies the sidecar self-exits ONLY
// when its original controlling parent is reliably dead across consecutive
// checks, and NEVER for a healthy sidecar or an unknown/rootless ppid.
import assert from "node:assert/strict";
import { parentIsGone, shouldSelfExit, shouldSkipBeatForDeadParent } from "../claude-channel.js";

// --- parentIsGone: rootless / unknown ppid must NEVER self-kill ---
assert.equal(
  parentIsGone({ originalPpid: 0, isAlive: () => false }),
  false,
  "originalPpid=0 (unknown) must never self-kill",
);
assert.equal(
  parentIsGone({ originalPpid: 1, isAlive: () => false }),
  false,
  "originalPpid=1 (rootless/reparented-to-init) must never self-kill",
);
assert.equal(
  parentIsGone({ originalPpid: undefined, isAlive: () => false }),
  false,
  "originalPpid=undefined must never self-kill",
);
assert.equal(
  parentIsGone({ originalPpid: null, isAlive: () => false }),
  false,
  "originalPpid=null must never self-kill",
);

// --- parentIsGone: healthy sidecar (parent alive) must NOT trip ---
assert.equal(
  parentIsGone({ originalPpid: 4242, isAlive: () => true }),
  false,
  "a real ppid with a LIVE parent must not be considered gone",
);

// --- parentIsGone: real parent that is now dead -> gone ---
assert.equal(
  parentIsGone({ originalPpid: 4242, isAlive: () => false }),
  true,
  "a real ppid with a DEAD parent must be considered gone",
);

// --- parentIsGone: missing isAlive is treated conservatively (not gone) ---
assert.equal(
  parentIsGone({ originalPpid: 4242 }),
  false,
  "missing isAlive must be treated conservatively (never self-kill)",
);

// --- shouldSelfExit: consecutive-miss latch ---
assert.equal(shouldSelfExit(0, 3), false, "0 misses must not self-exit");
assert.equal(shouldSelfExit(1, 3), false, "1 miss must not self-exit");
assert.equal(shouldSelfExit(2, 3), false, "2 misses (below threshold) must not self-exit");
assert.equal(shouldSelfExit(3, 3), true, "reaching the threshold must self-exit");
assert.equal(shouldSelfExit(5, 3), true, "exceeding the threshold must self-exit");
// default threshold is 2 (sped up 2026-06-05: 3s x 2 ~= 6s self-exit, was 30s x 3 ~= 90s)
assert.equal(shouldSelfExit(1), false, "default threshold (2): 1 miss must not self-exit");
assert.equal(shouldSelfExit(2), true, "default threshold (2): 2 misses must self-exit");

// --- End-to-end of the latch using an isAlive that flips, simulating the
// counter reset on a transient live read (no false positive). ---
{
  const reads = [false, false, true, false, false, false]; // a transient "alive" resets
  let i = 0;
  const isAlive = () => reads[i++];
  let misses = 0;
  let exited = false;
  for (let tick = 0; tick < reads.length; tick++) {
    if (parentIsGone({ originalPpid: 9999, isAlive })) misses += 1;
    else misses = 0;
    if (shouldSelfExit(misses, 3)) { exited = true; break; }
  }
  assert.equal(exited, true, "three consecutive dead reads after a transient live read must self-exit");
}
{
  // Two dead then alive forever -> never self-exits (no false positive).
  const reads = [false, false, true, true, true, true];
  let i = 0;
  const isAlive = () => reads[i++ % reads.length];
  let misses = 0;
  let exited = false;
  for (let tick = 0; tick < 20; tick++) {
    if (parentIsGone({ originalPpid: 9999, isAlive })) misses += 1;
    else misses = 0;
    if (shouldSelfExit(misses, 3)) { exited = true; break; }
  }
  assert.equal(exited, false, "a recovering/alive parent must never trip the guard");
}

// --- shouldSkipBeatForDeadParent: orphan-sidecar liveness-beat gate ---
// Skip the beat immediately when the controlling parent is known (>1) and dead.
assert.equal(
  shouldSkipBeatForDeadParent(4242, () => false),
  true,
  "real ppid with a DEAD parent -> skip the liveness beat",
);
assert.equal(
  shouldSkipBeatForDeadParent(4242, () => true),
  false,
  "real ppid with a LIVE parent -> beat normally",
);
// Unknown/rootless ppid must NEVER skip (always beat) -- conservative.
assert.equal(shouldSkipBeatForDeadParent(0, () => false), false, "ppid=0 (unknown) -> always beat");
assert.equal(shouldSkipBeatForDeadParent(1, () => false), false, "ppid=1 (rootless) -> always beat");
assert.equal(shouldSkipBeatForDeadParent(undefined, () => false), false, "ppid=undefined -> always beat");
assert.equal(shouldSkipBeatForDeadParent(null, () => false), false, "ppid=null -> always beat");
// Missing isAlive -> conservative, never skip.
assert.equal(shouldSkipBeatForDeadParent(4242), false, "missing isAlive -> beat (conservative)");
// A transient false read just skips one beat; recovery resumes beating.
{
  const reads = [false, true, false]; // dead, then alive, then dead
  let i = 0;
  const isAlive = () => reads[i++];
  assert.equal(shouldSkipBeatForDeadParent(9999, isAlive), true, "tick1: dead -> skip");
  assert.equal(shouldSkipBeatForDeadParent(9999, isAlive), false, "tick2: alive -> beat resumes");
  assert.equal(shouldSkipBeatForDeadParent(9999, isAlive), true, "tick3: dead again -> skip");
}

console.log("claude-channel-parent-guard.test.js: all assertions passed");
