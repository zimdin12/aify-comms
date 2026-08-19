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
import fs from "node:fs";
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

test("claude-aify leaves the operator's MCP servers alone unless strict mode is asked for", () => {
  // Always-strict was the old behaviour and it cost operators their own MCP servers — a
  // wrapper-launched claude lost the full ~/.claude.json list with no indication why. Two structural
  // guards assert the gate exists in the text; this asserts the flag's presence on the command line,
  // which is the thing that actually decides what claude loads.
  const relaxed = run({});
  assert.ok(
    !relaxed.argv.includes("--strict-mcp-config"),
    `default launch must not be strict: ${JSON.stringify(relaxed.argv)}`,
  );

  const strict = run({ env: { AIFY_CLAUDE_STRICT_MCP: "1" } });
  assert.ok(strict.argv.includes("--strict-mcp-config"), "the escape hatch must still work");
  const i = strict.argv.indexOf("--mcp-config");
  assert.ok(i >= 0, "strict mode must supply the two-server config it restricts claude to");
  assert.ok(strict.argv[i + 1], "and name the file");
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

// ── Session-handle validation, both branches ────────────────────────────────
//
// The five tests that used to guard this greped install.sh for substrings and all went red the day
// the wrapper body moved to a template, though behaviour was byte-identical. They are rewritten
// against the rendered artifact in service/tests/test_install_claude_session_validate.py; the two
// below are the part neither version could do — running it, and covering the branch where the id is
// GOOD, which nothing tested before.

const SEEDED_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

/** Seed a transcript so `validate_claude_session_id` finds one. */
function seedTranscript(home) {
  const dir = path.join(home, ".claude", "projects", "C--some-workspace");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${SEEDED_ID}.jsonl`), "{}\n");
}

test("claude-aify KEEPS a session id that has a transcript behind it", () => {
  const r = run({ env: { CLAUDE_SESSION_ID: SEEDED_ID }, prepareHome: seedTranscript });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.CLAUDE_SESSION_ID, SEEDED_ID, "a valid id must survive to the runtime");
  assert.doesNotMatch(r.stderr, /has no transcript/, "and must not be reported stale");
});

test("claude-aify forwards an explicit --resume only when the id validates", () => {
  const good = run({ args: ["--resume", SEEDED_ID], prepareHome: seedTranscript });
  const i = good.argv.indexOf("--resume");
  assert.ok(i >= 0, `--resume must be forwarded for a valid id: ${JSON.stringify(good.argv)}`);
  assert.equal(good.argv[i + 1], SEEDED_ID);

  // The stale case is the one that matters: leaving the handle in argv makes claude exit with
  // "No conversation found" instead of starting a fresh, repairable session.
  const stale = run({ args: ["--resume", "99999999-9999-9999-9999-999999999999"] });
  assert.equal(stale.launched, true, stale.stderr);
  assert.ok(
    !stale.argv.includes("--resume"),
    `a stale --resume must be stripped, not forwarded: ${JSON.stringify(stale.argv)}`,
  );
  assert.ok(!stale.argv.some((a) => a.startsWith("99999999")), "and neither may the id itself");
});

test("claude-aify still launches when the identity lookup cannot reach the service", () => {
  // THE DEFECT THIS FILE WAS BUILT TO FIND (2026-08-19). Resuming without --aify-agent triggers a
  // lookup that asks the service which agent owns the handle. It ran as
  // `_aify_rec="$(curl ... | node ...)"` under `set -euo pipefail`, so the assignment inherited the
  // PIPELINE's status and an unreachable service ENDED THE WRAPPER: no claude, no message, just
  // curl's exit code. Every other curl in install.sh already ended `|| true`; these three did not.
  //
  // It survived every text guard because the line was present and correct-looking, and it survived a
  // hand probe because that shell had AIFY_AGENT_ID exported, which skips the block entirely. Only a
  // sealed environment reaches it.
  const r = run({
    args: ["--resume", SEEDED_ID],
    env: { AIFY_COMMS_URL: NOWHERE_URL },
    prepareHome: seedTranscript,
  });
  assert.equal(r.launched, true, `an unreachable service must not stop a launch (exit ${r.status})`);
  assert.equal(r.env.AIFY_AGENT_ID, undefined, "and recovery legitimately found nothing");
  assert.match(r.stderr, /NO AGENT ID/, "so the session is anonymous, and says so");
});

test("claude-aify clears a session id with no transcript behind it", () => {
  // HOME is sealed to an empty dir, so no id can validate. A stale CLAUDE_SESSION_ID must be dropped
  // rather than passed on, or the bridge registers a handle that resolves to nothing.
  const r = run({ env: { CLAUDE_SESSION_ID: "00000000-0000-0000-0000-000000000000" } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.CLAUDE_SESSION_ID, undefined, "a handle with no transcript must not be exported");
  assert.match(r.stderr, /has no transcript/, "and the wrapper must say why");
});
