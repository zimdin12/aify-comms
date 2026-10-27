#!/usr/bin/env node
// The harness wrapper contract, proven by running the wrapper.
//
// Spec: docs/superpowers/specs/2026-08-19-harness-wrapper-contract.md. The wrapper stops knowing it
// belongs to aify-comms and starts reading a named set of inputs a HOST supplies. `HARNESS_ENDPOINT`
// and `HARNESS_MCP_COMMAND` are the two that matter: they are the client tier saying "point me at an
// environment" without knowing which one, which is what "external environments can connect" requires.
//
// THE CONSTRAINT THESE TESTS EXIST FOR: with no HARNESS_* variable set, behaviour must be exactly what
// it was. A live fleet of 45 agents launches through this wrapper, and the legacy AIFY_* names are
// what every one of them uses. `claude-wrapper-behaviour.test.js` is the other half of that promise —
// it asserts the legacy paths still work; this file asserts the new ones do too, and that the two
// agree on precedence rather than fighting.

import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import {
  NOWHERE_URL,
  reducedPath,
  renderWrapper,
  runWrapper,
  runtimeReachable,
} from "./wrapper-harness.mjs";

const claudeWrapper = () => path.join(renderWrapper("claude"), "claude-aify");
const run = (opts = {}) => runWrapper(claudeWrapper(), { runtimeName: "claude", ...opts });

// ── --check: validate, report, start nothing ────────────────────────────────

test("--check reports the resolved configuration and starts nothing", () => {
  // Not optional politeness. This repo's standing rule is "never run a bare aify-comms" precisely
  // because running a launcher to see whether it worked registered a bridge and reaped a live fleet.
  // A wrapper needs a way to be asked without being run.
  const r = run({ args: ["--check"], env: { HARNESS_IDENTITY: "probe-agent" } });
  assert.equal(r.launched, false, "--check must not launch the runtime");
  assert.equal(r.status, 0, "a valid configuration checks out clean");
  const out = `${r.stdout}${r.stderr}`;
  assert.match(out, /claude-code/, "it must say which runtime it is");
  assert.match(out, /probe-agent/, "and which identity it resolved");
  assert.match(out, /127\.0\.0\.2:1/, "and which endpoint");
});

test("--check reports the wrapper's own version", () => {
  // Version skew is what install.sh's single-step install used to prevent for free. Once the wrapper
  // ships separately, this is how a host can tell what it has.
  const r = run({ args: ["--check"] });
  assert.match(`${r.stdout}${r.stderr}`, /\d+\.\d+\.\d+/, "--check must report a version");
});

test("--check is consumed by the wrapper, never forwarded", () => {
  const r = run({ args: ["--check"] });
  assert.equal(r.launched, false);
  // And on a normal launch it is absent because it was never passed — the guard is that a launch
  // with other flags does not somehow acquire it.
  const normal = run({ args: ["--print"] });
  assert.ok(!normal.argv.includes("--check"), "a launch must not carry --check");
});

// ── HARNESS_ENDPOINT ────────────────────────────────────────────────────────

test("HARNESS_ENDPOINT sets the service the wrapper points the runtime at", () => {
  const r = run({ env: { HARNESS_ENDPOINT: "http://127.0.0.2:2/base" } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_COMMS_URL, "http://127.0.0.2:2/base");
});

test("HARNESS_ENDPOINT wins over the legacy AIFY_COMMS_URL when both are set", () => {
  const r = run({
    env: { HARNESS_ENDPOINT: "http://127.0.0.2:2/harness", AIFY_COMMS_URL: NOWHERE_URL },
  });
  assert.equal(r.env.AIFY_COMMS_URL, "http://127.0.0.2:2/harness", "the contract name is canonical");
});

test("an explicitly empty HARNESS_ENDPOINT is a configuration error, not a default", () => {
  // Exit 78 is the contract's "configuration invalid". Falling back to the baked URL here would be
  // worse than failing: the host asked for no endpoint, and silently substituting one sends an
  // agent's traffic somewhere the host did not choose.
  const r = run({ env: { HARNESS_ENDPOINT: "" }, args: ["--print"] });
  assert.equal(r.launched, false, "a wrapper must not launch on an invalid configuration");
  assert.equal(r.status, 78, `expected exit 78, got ${r.status}: ${r.stderr}`);
  assert.match(r.stderr, /HARNESS_ENDPOINT/, "and must name the input that is wrong");
});

// ── HARNESS_IDENTITY / HARNESS_ROLE ─────────────────────────────────────────

test("HARNESS_IDENTITY and HARNESS_ROLE carry the agent's identity", () => {
  const r = run({ env: { HARNESS_IDENTITY: "harness-agent", HARNESS_ROLE: "architect" } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, "harness-agent");
  assert.equal(r.env.AIFY_AGENT_ROLE, "architect");
});

test("HARNESS_IDENTITY wins over the legacy AIFY_AGENT_ID", () => {
  const r = run({ env: { HARNESS_IDENTITY: "harness-agent", AIFY_AGENT_ID: "legacy-agent" } });
  assert.equal(r.env.AIFY_AGENT_ID, "harness-agent");
});

test("the --aify-agent flag still wins over both", () => {
  // The flag is what the dashboard's copyable command uses, and an explicit argument beating ambient
  // environment is the ordering an operator expects.
  const r = run({
    args: ["--aify-agent", "flag-agent"],
    env: { HARNESS_IDENTITY: "harness-agent", AIFY_AGENT_ID: "legacy-agent" },
  });
  assert.equal(r.env.AIFY_AGENT_ID, "flag-agent");
});

// ── HARNESS_EXTRA_ENV ───────────────────────────────────────────────────────

test("HARNESS_EXTRA_ENV exports host-supplied pairs verbatim", () => {
  const r = run({ env: { HARNESS_EXTRA_ENV: "FOO=bar\nBAZ=qux quux" } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.FOO, "bar");
  assert.equal(r.env.BAZ, "qux quux", "a value with a space must survive whole");
});

test("HARNESS_EXTRA_ENV ignores blank and malformed entries instead of failing the launch", () => {
  // A host's config file with a trailing newline must not stop an agent starting.
  const r = run({ env: { HARNESS_EXTRA_ENV: "\n\nGOOD=1\nnot-a-pair\n" } });
  assert.equal(r.launched, true, `malformed extra env must not be fatal: ${r.stderr}`);
  assert.equal(r.env.GOOD, "1");
});

// ── HARNESS_MCP_COMMAND ─────────────────────────────────────────────────────

test("HARNESS_MCP_COMMAND names the bridge the runtime is pointed at", () => {
  // The pivot of the whole contract: today the wrapper knows it must load
  // ~/.aify-comms/mcp/stdio/server.js. Under the contract it loads whatever the host names.
  const r = run({
    args: ["--aify-agent", "probe-agent"],
    env: { AIFY_CLAUDE_STRICT_MCP: "1", HARNESS_MCP_COMMAND: "node /custom/bridge.js" },
  });
  assert.equal(r.launched, true, r.stderr);
  assert.ok(r.argv.includes("--mcp-config"), `strict mode must write an MCP config: ${JSON.stringify(r.argv)}`);
  const config = r.files["--mcp-config"] || "";
  assert.match(config, /\/custom\/bridge\.js/, "the host's bridge command must reach the config");
});

test("without HARNESS_MCP_COMMAND the built-in bridge path is used unchanged", () => {
  // Back-compat, and deliberately a different code path: the default keeps the existing quoting of a
  // filesystem path, which survives spaces. Only the host-supplied form is word-split.
  const r = run({ args: ["--aify-agent", "probe-agent"], env: { AIFY_CLAUDE_STRICT_MCP: "1" } });
  assert.ok(r.argv.includes("--mcp-config"));
  const config = r.files["--mcp-config"] || "";
  assert.match(config, /mcp[/\\]stdio[/\\]server\.js/, "the built-in bridge must still be named");
  assert.match(config, /claude-channel\.js/, "and the channel server with it");
});

// ── Exit codes ──────────────────────────────────────────────────────────────

test("a missing runtime CLI exits 127 and says which one", (t) => {
  // Guarded, because getting this wrong means launching the operator's REAL claude from a test suite.
  const dir = renderWrapper("claude");
  const probePath = reducedPath(path.join(dir, "stub-bin"));
  if (runtimeReachable("claude", probePath)) {
    t.skip("a real `claude` is reachable under the reduced PATH — refusing to risk launching it");
    return;
  }
  const r = runWrapper(path.join(dir, "claude-aify"), {
    runtimeName: "claude",
    withStub: false,
    minimalPath: true,
  });
  assert.equal(r.status, 127, `expected 127, got ${r.status}: ${r.stderr}`);
  assert.match(r.stderr, /claude/, "the message must name the missing CLI");
});
