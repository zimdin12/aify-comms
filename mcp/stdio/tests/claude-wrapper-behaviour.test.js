#!/usr/bin/env node
// What the claude-aify wrapper DOES, proven by running it.
//
// The companion file `claude-wrapper-determinism.test.js` reads the rendered text. This one puts a stub
// named `claude` on PATH, runs the real wrapper, and reads back the argv and environment the runtime
// was actually launched with. The difference matters: a text guard cannot tell you an export is
// reachable, that argv survived the parser, or that a flag ended up on the command line rather than in
// a variable nothing expands.
//
// This is the regression net for the HARNESS_* parameterisation (v0.6 Phase 2, Task 2.2b). Every
// assertion here describes behaviour that must be IDENTICAL before and after the contract lands when no
// HARNESS_* variable is set — which is the only way an operator's live fleet is safe from the refactor.

import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { NOWHERE_URL, renderWrapper, runWrapper } from "./wrapper-harness.mjs";

function claudeWrapper() {
  const dir = renderWrapper("claude");
  return path.join(dir, "claude-aify");
}

const run = (opts = {}) => runWrapper(claudeWrapper(), { runtimeName: "claude", ...opts });

test("claude-aify launches the runtime and forwards argv", () => {
  const r = run({ args: ["--print", "hello world"] });
  assert.equal(r.launched, true, `wrapper never reached claude:\n${r.stderr}`);
  assert.ok(r.argv.includes("--print"), `argv missing --print: ${JSON.stringify(r.argv)}`);
  assert.ok(r.argv.includes("hello world"), "an argument containing a space must survive as ONE argv entry");
});

test("claude-aify passes the runtime's exit code through unchanged", () => {
  const r = run({ stubExitCode: 42 });
  assert.equal(r.launched, true);
  assert.equal(r.status, 42, "the runtime's own exit code is the wrapper's exit code");
});

test("claude-aify exports the identity the bridge registers under", () => {
  const r = run({ args: ["--aify-agent", "probe-agent", "--aify-role", "tester"] });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, "probe-agent");
  assert.equal(r.env.AIFY_AGENT_ROLE, "tester");
  assert.equal(r.env.AIFY_RUNTIME, "claude-code");
});

test("claude-aify accepts identity from the environment as well as the flag", () => {
  const r = run({ env: { AIFY_AGENT_ID: "env-agent", AIFY_AGENT_ROLE: "architect" } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, "env-agent");
  assert.equal(r.env.AIFY_AGENT_ROLE, "architect");
});

test("claude-aify defaults the role rather than exporting an empty one", () => {
  const r = run({ args: ["--aify-agent", "probe-agent"] });
  assert.equal(r.env.AIFY_AGENT_ROLE, "coder", "an unset role must fall back, not export empty");
});

test("claude-aify exports no identity at all for an anonymous session, and says so", () => {
  // Anonymous sessions are legal. What is NOT legal is being silent about it: without an agent id
  // every turn-state path is dead while the channel sidecar can still latch `working` forever.
  const r = run({});
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, undefined, "no id must mean no export, not an empty export");
  assert.match(r.stderr, /NO AGENT ID/, "and the wrapper must warn");
});

test("claude-aify carries the endpoint to the runtime, caller env winning over the baked value", () => {
  const r = run({ env: { AIFY_COMMS_URL: NOWHERE_URL } });
  assert.equal(r.env.AIFY_COMMS_URL, NOWHERE_URL);
});

test("claude-aify falls back to the endpoint baked in at install time", () => {
  // Render-time URL is 127.0.0.1:8899. With the caller's value removed the baked one must apply — an
  // empty AIFY_COMMS_URL silently costs every reply a 120s stale-window wait rather than failing.
  const r = run({ env: { AIFY_COMMS_URL: undefined } });
  assert.equal(r.env.AIFY_COMMS_URL, "http://127.0.0.1:8899");
});

test("claude-aify applies the unattended bypass by default and --safe removes it", () => {
  const on = run({});
  assert.ok(
    on.argv.includes("--dangerously-skip-permissions"),
    `bypass must be on by default: ${JSON.stringify(on.argv)}`,
  );
  const off = run({ args: ["--safe"] });
  assert.ok(!off.argv.includes("--dangerously-skip-permissions"), "--safe must remove it");
  assert.ok(!off.argv.includes("--safe"), "--safe is consumed by the wrapper, not forwarded");
});

test("claude-aify resolves session mode and marks channels enabled", () => {
  // stdin is not a TTY under spawnSync, so the auto-detect lands on managed. An explicit flag wins.
  const auto = run({});
  assert.equal(auto.env.AIFY_SESSION_MODE, "managed", "no TTY must auto-detect managed");
  const explicit = run({ args: ["--resident"] });
  assert.equal(explicit.env.AIFY_SESSION_MODE, "resident", "--resident must override the detect");
  assert.equal(explicit.env.AIFY_CHANNELS_ENABLED, "1");
});

test("claude-aify loads the channel server that resident wake depends on", () => {
  const r = run({ args: ["--resident"] });
  const i = r.argv.indexOf("--dangerously-load-development-channels");
  assert.ok(i >= 0, `channel flag missing: ${JSON.stringify(r.argv)}`);
  assert.equal(r.argv[i + 1], "server:aify-comms-channel", "and it must name the channel server");
});

test("claude-aify installs the session-capture hooks on the launch it performs", () => {
  const r = run({ args: ["--aify-agent", "probe-agent"] });
  const i = r.argv.indexOf("--settings");
  assert.ok(i >= 0, `--settings missing: ${JSON.stringify(r.argv)}`);
  assert.ok(r.argv[i + 1], "a settings file must be named");
});

test("claude-aify applies a managed model override only when the caller has not chosen one", () => {
  const injected = run({ env: { AIFY_MANAGED_MODEL: "opus" } });
  const i = injected.argv.indexOf("--model");
  assert.ok(i >= 0, "the managed model must be injected");
  assert.equal(injected.argv[i + 1], "opus");

  const explicit = run({ env: { AIFY_MANAGED_MODEL: "opus" }, args: ["--model", "haiku"] });
  assert.equal(
    explicit.argv.filter((a) => a === "--model").length,
    1,
    "an explicit --model must not be duplicated by the injection",
  );
  assert.ok(explicit.argv.includes("haiku"), "and the caller's choice must be the one that survives");
});

test("claude-aify clears a session id with no transcript behind it", () => {
  // HOME is sealed to an empty dir, so no id can validate. A stale CLAUDE_SESSION_ID must be dropped
  // rather than passed on, or the bridge registers a handle that resolves to nothing.
  const r = run({ env: { CLAUDE_SESSION_ID: "00000000-0000-0000-0000-000000000000" } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.CLAUDE_SESSION_ID, undefined, "a handle with no transcript must not be exported");
  assert.match(r.stderr, /has no transcript/, "and the wrapper must say why");
});
