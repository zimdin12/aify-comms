#!/usr/bin/env node
// What the codex-aify wrapper DOES, proven by running it.
//
// The regression net for putting codex on the harness contract (v0.6 Phase 2, Task 2.3). Same shape as
// claude-wrapper-behaviour.test.js and the same reason: `codex-wrapper-determinism.test.js` reads the
// rendered text, and text cannot tell you a flag reached the command line or an export was reachable.
//
// CODEX IS HARDER TO RUN THAN CLAUDE, and the difference is the interesting part. Its wrapper starts
// `codex app-server` in the background on an ephemeral port and REFUSES TO CONTINUE until that port
// accepts a connection. So the stub has to be two programs: a TCP listener when invoked as the
// app-server, and a recorder when invoked in the foreground. Anything less and every assertion here
// would be about a wrapper that exited with "could not reach the local app-server" — which is exactly
// the kind of green-looking nothing this file exists to avoid.

import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { NOWHERE_URL, renderWrapper, runWrapper } from "./wrapper-harness.mjs";

// Invoked as the app-server: hold the port open and never return. Invoked otherwise: fall through to
// the recorder. `exec` matters — the wrapper kills this PID on cleanup, and a shell that had forked
// node would leave the listener behind.
const APP_SERVER_PRELUDE = [
  'for _a in "$@"; do',
  '  if [ "$_a" = "app-server" ]; then',
  '    _port=""',
  '    for _b in "$@"; do',
  '      case "$_b" in *://127.0.0.1:*) _port="${_b##*:}" ;; esac',
  '    done',
  '    [ -n "$_port" ] || exit 1',
  '    exec node -e \'const net=require("net");net.createServer(()=>{}).listen(Number(process.argv[1]),"127.0.0.1");setInterval(()=>{},1e9)\' "$_port"',
  '  fi',
  'done',
].join("\n");

const codexWrapper = () => path.join(renderWrapper("codex"), "codex-aify");
const run = (opts = {}) =>
  runWrapper(codexWrapper(), { runtimeName: "codex", stubPrelude: APP_SERVER_PRELUDE, ...opts });

test("codex-aify launches the runtime and forwards argv", () => {
  const r = run({ args: ["--print", "hello world"] });
  assert.equal(r.launched, true, `wrapper never reached codex:\n${r.stderr}`);
  assert.ok(r.argv.includes("--print"), `argv missing --print: ${JSON.stringify(r.argv)}`);
  assert.ok(r.argv.includes("hello world"), "an argument containing a space must survive as ONE entry");
});

test("codex-aify points the runtime at the app-server it started", () => {
  // `--remote ws://127.0.0.1:<port>` is how the foreground TUI reaches the app-server this wrapper
  // owns. Without it codex starts its own, and the bridge's live-binding discovery finds the wrong one.
  const r = run({});
  const i = r.argv.indexOf("--remote");
  assert.ok(i >= 0, `--remote missing: ${JSON.stringify(r.argv)}`);
  assert.match(r.argv[i + 1], /^ws:\/\/127\.0\.0\.1:\d+$/, "and it must name the local app-server");
  assert.equal(
    r.env.AIFY_CODEX_APP_SERVER_URL,
    r.argv[i + 1],
    "the bridge reads this from the environment; the two must agree or discovery binds nothing",
  );
});

test("codex-aify exports the identity the bridge registers under", () => {
  const r = run({ args: ["--aify-agent", "probe-agent", "--aify-role", "tester"] });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, "probe-agent");
  assert.equal(r.env.AIFY_AGENT_ROLE, "tester");
  assert.equal(r.env.AIFY_RUNTIME, "codex");
});

test("codex-aify defaults the role rather than exporting an empty one", () => {
  const r = run({ args: ["--aify-agent", "probe-agent"] });
  assert.equal(r.env.AIFY_AGENT_ROLE, "coder");
});

test("codex-aify carries the endpoint, caller env winning over the baked value", () => {
  const override = run({ env: { AIFY_COMMS_URL: NOWHERE_URL } });
  assert.equal(override.env.AIFY_COMMS_URL, NOWHERE_URL);

  const baked = run({ env: { AIFY_COMMS_URL: undefined } });
  assert.equal(baked.env.AIFY_COMMS_URL, "http://127.0.0.1:8899", "the install-time URL must apply");
});

test("codex-aify applies the unattended bypass by default and --safe removes it", () => {
  // 2026-06-02: aify agents run unattended and must not stall on approval prompts. For codex the flag
  // must reach BOTH invocations — the app-server and the foreground TUI — or the approval appears in
  // the half that was missed.
  const on = run({});
  assert.ok(
    on.argv.includes("--dangerously-bypass-approvals-and-sandbox"),
    `bypass must be on by default: ${JSON.stringify(on.argv)}`,
  );
  const off = run({ args: ["--safe"] });
  assert.ok(!off.argv.includes("--dangerously-bypass-approvals-and-sandbox"), "--safe must remove it");
  assert.ok(!off.argv.includes("--safe"), "--safe is consumed by the wrapper, not forwarded");
});

test("codex-aify resolves a session mode", () => {
  const auto = run({});
  assert.equal(auto.env.AIFY_SESSION_MODE, "managed", "no TTY must auto-detect managed");
  const explicit = run({ args: ["--resident"] });
  assert.equal(explicit.env.AIFY_SESSION_MODE, "resident");
});

test("codex-aify still launches when the identity lookup cannot reach the service", () => {
  // The same defect fixed in the claude wrapper: `_aify_codex_rec="$(curl ... | node ...)"` under
  // `set -euo pipefail` inherits the pipeline's status, so an unreachable service ended the wrapper
  // with no runtime and no message.
  const r = run({ args: ["--resume", "01999999-9999-7999-8999-999999999999"], env: { AIFY_COMMS_URL: NOWHERE_URL } });
  assert.notEqual(r.status, 7, "an unreachable service must not surface as curl's exit code");
  assert.notEqual(r.status, 28, "nor as curl's timeout");
});

test("codex-aify passes the runtime's exit code through unchanged", () => {
  const r = run({ stubExitCode: 42 });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.status, 42);
});
