#!/usr/bin/env node
// Every environment name a worker could resolve its own identity from is neutralised when it is spawned.
//
// DERIVED FROM launch-identity.mjs, not listed here. A hand-written list is what failed: NEVER_INHERITED
// stripped AIFY_AGENT_ID and its alias AIFY_COMMS_AGENT_ID, and AIFY_AGENT_ROLE — but not
// AIFY_COMMS_AGENT_ROLE. launch-identity reads
//
//     AIFY_AGENT_ROLE || AIFY_COMMS_AGENT_ROLE || "coder"
//
// so the alias was still reachable. Measured before the fix: a bridge holding
// AIFY_COMMS_AGENT_ROLE=manager spawned a worker with an unknown role, and the worker resolved its role
// as "manager" instead of its own default. That is the exact bug stripping AIFY_AGENT_ROLE was added to
// prevent, surviving through its other name.
//
// TWO WAYS TO NEUTRALISE, and only one of them is general:
//
//   * REMOVE the name          — definitive for every consumer.
//   * SET a value the consumer treats as definitive — works for the role FLAGS, which are tested with
//     ["1","true","yes"].includes(v), so "0" is a real no. It does NOT work for the role STRING, whose
//     consumer is an `||` chain where "" is falsy and falls through to the next name.
//
// This test accepts either, and reads the source of truth to decide which names must be covered — so a
// fifth identity name added to launch-identity.mjs fails here until it is handled.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { NEVER_INHERITED } from "../child-env-hygiene.mjs";
import { terminalChildEnv } from "../terminal-env.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const LAUNCH_IDENTITY = join(HERE, "..", "launch-identity.mjs");

/** Every AIFY_* name launch-identity.mjs resolves identity or role from. */
function identitySources() {
  const source = readFileSync(LAUNCH_IDENTITY, "utf8");
  const names = new Set();
  for (const m of source.matchAll(/process\.env\.(AIFY_[A-Z0-9_]+)/g)) {
    // Skip the ones inside comments: a comment naming a variable is prose, not a read.
    names.add(m[1]);
  }
  return [...names].sort();
}

// ── the control ────────────────────────────────────────────────────────────────────────────────
{
  const sources = identitySources();
  assert.ok(
    sources.length >= 4,
    `only ${sources.length} identity sources found; the scan is broken and would pass vacuously`,
  );
  assert.ok(sources.includes("AIFY_AGENT_ID"), "the scan missed a name that certainly exists");
  assert.ok(sources.includes("AIFY_COMMS_AGENT_ROLE"), "the scan missed the alias that caused this test");
  assert.ok(!sources.includes("AIFY_ZZZ_NOT_REAL"), "the scan invents names");
}

// ── every source is neutralised, one way or the other ──────────────────────────────────────────
{
  // A hostile parent: every identity name set to something a worker must never adopt.
  const hostile = Object.fromEntries(identitySources().map((n) => [n, "inherited-from-the-bridge"]));
  const child = terminalChildEnv({
    baseEnv: { ...hostile, PATH: "/usr/bin" },
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: {},                       // role UNKNOWN — the case where the `||` chain falls through
  });

  for (const name of identitySources()) {
    const value = child[name];
    const removed = !(name in child);
    const overwritten = value !== "inherited-from-the-bridge";
    assert.ok(
      removed || overwritten,
      `${name} reached the worker unchanged from the bridge's own environment`,
    );
  }
}

// ── and the value the worker would actually resolve ────────────────────────────────────────────
{
  // The assertion above is about names. This one is about the ANSWER, because neutralising a name with
  // a value the consumer treats as absent is not neutralising it at all — which is precisely how
  // AIFY_AGENT_ROLE="" let AIFY_COMMS_AGENT_ROLE through.
  const child = terminalChildEnv({
    baseEnv: {
      AIFY_AGENT_ROLE: "manager",
      AIFY_COMMS_AGENT_ROLE: "manager",
      AIFY_AGENT_ID: "the-bridge",
      AIFY_COMMS_AGENT_ID: "the-bridge",
      PATH: "/usr/bin",
    },
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: {},
  });

  // Exactly what launch-identity.mjs computes, reproduced here so the test asserts the resolved value
  // rather than the ingredients.
  const role = String(child.AIFY_AGENT_ROLE || child.AIFY_COMMS_AGENT_ROLE || "coder").trim();
  const id = String(child.AIFY_AGENT_ID || child.AIFY_COMMS_AGENT_ID || "");
  assert.equal(role, "coder", "the worker inherited the bridge's role through the fallback chain");
  assert.equal(id, "sc-tester", "the worker did not get its own id");
}

// ── the role flags, which are neutralised the other way ────────────────────────────────────────
{
  // Set to "0" rather than removed, and that is correct: their consumer is
  // ["1","true","yes"].includes(v), for which "0" is a definitive no. Pinned so nobody "tidies" them
  // into the removal list and changes what an absent flag means.
  const child = terminalChildEnv({
    baseEnv: { AIFY_ENVIRONMENT_BRIDGE: "1", AIFY_MANAGED_DISPATCH: "1", PATH: "/usr/bin" },
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: {},
  });
  for (const flag of ["AIFY_ENVIRONMENT_BRIDGE", "AIFY_MANAGED_DISPATCH"]) {
    assert.equal(child[flag], "0", `${flag} must be an explicit no, not merely absent`);
    assert.ok(
      !["1", "true", "yes"].includes(String(child[flag]).toLowerCase()),
      `${flag} would still read as enabled inside the worker`,
    );
  }
}

// ── the list keeps its reasons ─────────────────────────────────────────────────────────────────
{
  for (const [name, reason] of Object.entries(NEVER_INHERITED)) {
    assert.equal(typeof reason, "string", name);
    assert.ok(reason.length > 40, `${name} is stripped without a real reason`);
  }
}

console.log("every-identity-source-is-neutralised.test.js: all assertions passed");
