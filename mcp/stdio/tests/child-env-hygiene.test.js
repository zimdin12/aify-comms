#!/usr/bin/env node
// A worker does not inherit the bridge's own ancestry.
//
// Two variables have been caught leaking this way, one at a time, each after it caused a symptom
// somebody eventually noticed: a bridge's `AIFY_AGENT_ROLE` silently overwriting every spawn's role,
// and `CLAUDE_CODE_CHILD_SESSION` silently disabling transcripts for every managed agent. Seven places
// in this bridge build a child environment by spreading the parent's, and each guarded its own case
// separately, afterwards.
//
// The pattern is the defect. This states the list once so the third one is refused by construction.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NEVER_INHERITED,
  inheritedMarkersIn,
  withoutInheritedMarkers,
} from "../child-env-hygiene.mjs";

test("the markers behind both known incidents are on the list", () => {
  // Named explicitly: a general rule that quietly stopped covering the cases that produced it would
  // still pass every other test in this file.
  assert.ok("CLAUDE_CODE_CHILD_SESSION" in NEVER_INHERITED, "the transcript-loss marker");
  assert.ok("AIFY_AGENT_ROLE" in NEVER_INHERITED, "the role-overwrite marker");
});

test("every entry carries a reason, not just a name", () => {
  // A bare list invites the next reader to add one on suspicion and delete one that looks unused.
  for (const [name, reason] of Object.entries(NEVER_INHERITED)) {
    assert.equal(typeof reason, "string", name);
    assert.ok(reason.length > 40, `${name} has no real reason: ${reason}`);
  }
});

test("the markers are REMOVED, never blanked", () => {
  // An empty string is a value. A runtime asking "is this set?" reads "" as yes, which is how a
  // half-cleared marker would keep the original bug while looking fixed.
  const cleaned = withoutInheritedMarkers({
    CLAUDE_CODE_CHILD_SESSION: "1",
    AIFY_AGENT_ROLE: "manager",
    PATH: "/usr/bin",
  });
  assert.ok(!("CLAUDE_CODE_CHILD_SESSION" in cleaned), "the marker is still present, as an empty value");
  assert.ok(!("AIFY_AGENT_ROLE" in cleaned));
  assert.equal(cleaned.PATH, "/usr/bin", "an ordinary variable was dropped");
});

test("everything else survives, because a child needs what it inherits", () => {
  // The reason this is a denylist. An allowlist's failure mode is a worker that mysteriously cannot
  // reach something; this one's is bounded to a marker nobody has been bitten by yet.
  const parent = {
    PATH: "/usr/bin", HOME: "/home/dev", HTTPS_PROXY: "http://proxy:3128",
    ANTHROPIC_API_KEY: "secret", TERM: "xterm-256color", AIFY_SERVER_URL: "http://svc:8800",
    CLAUDE_CODE_CHILD_SESSION: "1",
  };
  const cleaned = withoutInheritedMarkers(parent);
  for (const key of ["PATH", "HOME", "HTTPS_PROXY", "ANTHROPIC_API_KEY", "TERM", "AIFY_SERVER_URL"]) {
    assert.equal(cleaned[key], parent[key], `${key} was dropped`);
  }
});

test("the caller's object is not mutated", () => {
  // Most call sites here spread `process.env`, so mutating the argument would reach the whole process.
  const parent = { CLAUDE_CODE_CHILD_SESSION: "1", PATH: "/usr/bin" };
  withoutInheritedMarkers(parent);
  assert.equal(parent.CLAUDE_CODE_CHILD_SESSION, "1", "the caller's environment was modified");
});

test("a clean environment passes through unchanged", () => {
  const parent = { PATH: "/usr/bin", HOME: "/home/dev" };
  assert.deepEqual(withoutInheritedMarkers(parent), parent);
});

test("missing and empty inputs are handled rather than thrown on", () => {
  assert.deepEqual(withoutInheritedMarkers(), {});
  assert.deepEqual(withoutInheritedMarkers({}), {});
});

test("a caller can ask WHICH markers were present, to say so out loud", () => {
  // Both known cases stayed invisible because the spawn quietly changed its child's configuration.
  const present = inheritedMarkersIn({
    CLAUDE_CODE_CHILD_SESSION: "1", AIFY_AGENT_ROLE: "manager", PATH: "/usr/bin",
  });
  assert.deepEqual(present.sort(), ["AIFY_AGENT_ROLE", "CLAUDE_CODE_CHILD_SESSION"]);
});

test("an empty or whitespace value does not count as present", () => {
  // Reporting "I dropped AIFY_AGENT_ROLE" when it was never meaningfully set is noise that trains a
  // reader to ignore the line.
  assert.deepEqual(inheritedMarkersIn({ AIFY_AGENT_ROLE: "", CLAUDE_CODE_CHILD_SESSION: "   " }), []);
  assert.deepEqual(inheritedMarkersIn({}), []);
});
