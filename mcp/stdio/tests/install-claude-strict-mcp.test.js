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
// The launcher's own BEHAVIOUR (the flag reaching or not reaching claude's command line, the managed
// model and effort) is proven by aify-wrapper's tests, which run its templates against a stub runtime.
// What stays here is the seam this installer owns: the strict config it renders carries the channel
// and points the comms tools at this service. The copies of aify-wrapper's tests that used to live in
// claude-wrapper-behaviour and claude-wrapper-contract were deleted on 2026-10-30.
import assert from "assert";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import { renderWrapper, runWrapper } from "./wrapper-harness.mjs";

const WRAPPER = path.join(renderWrapper("claude"), "claude-aify");
const SRC = fs.readFileSync(WRAPPER, "utf8");

test("the rendered wrapper is substantial enough for these assertions to mean anything", () => {
  // Guards against the whole file passing vacuously if the render ever produced a stub.
  assert.ok(SRC.split("\n").length > 100, "a plausible claude-aify is hundreds of lines");
});

test("claude-aify consumes the managed effort env", () => {
  assert.ok(/AIFY_MANAGED_EFFORT/.test(SRC), "expected claude-aify to read AIFY_MANAGED_EFFORT");
  assert.ok(/--effort/.test(SRC), "expected --effort to be passed when a managed effort is set");
});

test("strict mode loads the channel and no local tool bridge: the comms tools come from the service", () => {
  // 0.9.11: this installer renders the http arm, so `HARNESS_MCP_COMMAND` (the stdio arm's bridge override,
  // tested in aify-wrapper) has nothing to point. The strict config carries the channel alone, and the comms
  // tools are this launch's own `--mcp-config=` entry for the service's /mcp/http.
  const r = runWrapper(WRAPPER, { runtimeName: "claude", args: ["--aify-agent", "probe-agent"], env: { AIFY_CLAUDE_STRICT_MCP: "1" } });
  assert.equal(r.launched, true, r.stderr);
  assert.ok(r.argv.includes("--strict-mcp-config") && r.argv.includes("--mcp-config"), JSON.stringify(r.argv));
  const config = r.files["--mcp-config"] || "";
  assert.match(config, /claude-channel\.js/, "the channel server must be in the strict config");
  assert.doesNotMatch(config, /server\.js"/, "no local tool bridge may be started");
  assert.ok(r.argv.some((a) => a.startsWith("--mcp-config=")), "the service's comms tools must be added for this launch");
});
