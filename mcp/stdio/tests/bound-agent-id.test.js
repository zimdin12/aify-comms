#!/usr/bin/env node
// Tests that CALL `bound-agent-id.mjs` — the shared binding read extracted in v0.5.4 from three
// near-copies that had already drifted.
//
// THE DRIFT IS THE POINT. `claude-channel.js` fell back to "" when no binding file existed;
// `hermes-channel.js` and `hermes-managed-host.js` fell back to `AIFY_AGENT_ID`. Nothing recorded
// that as a decision — it is what three copies look like after separate edits. The shared reader
// therefore REQUIRES the fallback to be passed rather than defaulting it, so the difference is an
// argument at each call site instead of a body to diff.
//
// What is asserted here is the read and the boundary; which fallback each bridge SHOULD use is an
// open question for the operator, and these tests deliberately do not answer it.

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { writeAgentBindingFile } from "../binding-file.js";
import { boundAgentId, envAgentId } from "../bound-agent-id.mjs";

const dir = mkdtempSync(path.join(os.tmpdir(), "aify-bound-agent-"));
const originalEnv = process.env.AIFY_AGENT_ID;

try {
  // ── no binding file: the fallback is whatever the caller passed, and nothing else ──────────
  assert.equal(boundAgentId({ dir }), "", "the default fallback is empty — never the environment");

  process.env.AIFY_AGENT_ID = "env-agent";
  assert.equal(boundAgentId({ dir }), "",
    "an unpassed fallback must NOT silently become AIFY_AGENT_ID — that defaulting is exactly what "
    + "let the three copies disagree without anyone choosing");
  assert.equal(boundAgentId({ dir, fallback: envAgentId() }), "env-agent",
    "…and a caller that wants it says so");

  // ── a binding file wins over the fallback ─────────────────────────────────────────────────
  writeAgentBindingFile({ pid: process.ppid || process.pid, dir, agentId: "bound-agent" });
  assert.equal(boundAgentId({ dir }), "bound-agent");
  assert.equal(boundAgentId({ dir, fallback: envAgentId() }), "bound-agent",
    "the file is the binding; the fallback is only for when there is none");

  // ── envAgentId trims and never returns undefined ──────────────────────────────────────────
  process.env.AIFY_AGENT_ID = "  spaced  ";
  assert.equal(envAgentId(), "spaced");
  delete process.env.AIFY_AGENT_ID;
  assert.equal(envAgentId(), "", "an unset variable is the empty string, never 'undefined'");

  // ── an unreadable directory is a missing binding, not a crash ─────────────────────────────
  // A bridge that throws here does not start at all; a bridge that returns the fallback keeps going
  // and reports itself unbound, which is the recoverable failure.
  assert.doesNotThrow(() => boundAgentId({ dir: path.join(dir, "does", "not", "exist") }));
  assert.equal(boundAgentId({ dir: path.join(dir, "does", "not", "exist"), fallback: "fb" }), "fb");
} finally {
  if (originalEnv === undefined) delete process.env.AIFY_AGENT_ID;
  else process.env.AIFY_AGENT_ID = originalEnv;
  rmSync(dir, { recursive: true, force: true });
}

console.log("bound-agent-id.test.js: all assertions passed");
