// The claude-aify wrapper drops --strict-mcp-config by default and only adds it when
// AIFY_CLAUDE_STRICT_MCP=1. The legacy always-strict behaviour cost operators their own MCP servers:
// a wrapper-launched claude session lost the full ~/.claude.json list (aify-project-graph, github,
// browsermcp, …) with no indication why.
//
// CONSOLIDATED 2026-08-19 (v0.6 Phase 2). `claude-aify-strict-mcp-env-gate.test.js` asserted the same
// two things against the same file and is now folded in here, along with its third check on the
// managed model/effort passthrough. Nothing is lost: two files were reading one source and claiming
// one invariant twice.
//
// REPOINTED at the same time, and that is the more important change. These read install.sh's SOURCE.
// When the wrapper body moved into wrappers/claude-aify.sh.in they went red while the wrapper was
// proven byte-identical — a location pin breaks on a move and stays green on a defect. They now read
// the RENDERED wrapper, which is the artifact an operator actually runs, so a move cannot break them
// and a broken render cannot hide from them.
//
// The BEHAVIOUR — the flag genuinely reaching or not reaching claude's command line under each
// setting, and the managed model override — is proven by running the wrapper in
// claude-wrapper-behaviour.test.js, so the text copies of those were removed on 2026-09-18. What is
// left is the managed EFFORT, which nothing runs.
import assert from "assert";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import { renderWrapper } from "./wrapper-harness.mjs";

const SRC = fs.readFileSync(path.join(renderWrapper("claude"), "claude-aify"), "utf8");

test("the rendered wrapper is substantial enough for these assertions to mean anything", () => {
  // Guards against the whole file passing vacuously if the render ever produced a stub.
  assert.ok(SRC.split("\n").length > 100, "a plausible claude-aify is hundreds of lines");
});

test("claude-aify consumes the managed effort env", () => {
  // The managed MODEL is proven by running the wrapper in claude-wrapper-behaviour.test.js; the
  // effort has no running test, so its text guard stays.
  assert.ok(/AIFY_MANAGED_EFFORT/.test(SRC), "expected claude-aify to read AIFY_MANAGED_EFFORT");
  assert.ok(/--effort/.test(SRC), "expected --effort to be passed when a managed effort is set");
});
