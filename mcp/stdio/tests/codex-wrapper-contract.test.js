#!/usr/bin/env node
// The harness wrapper contract as codex-aify implements it.
//
// Same six inputs as claude, one deliberate difference: codex's MCP servers are registered at install
// time by `codex mcp add`, not by this launcher, so HARNESS_MCP_COMMAND is accepted and REPORTED AS
// UNUSED rather than silently swallowed. A wrapper that accepted an input and ignored it would be
// claiming to do a job it does not do, which is worse than not accepting it.
//
// The ordering constraint is the other difference and it is load-bearing: this wrapper's first act is
// to allocate a port and start a background `codex app-server`. Validation and --check therefore run
// AHEAD of all of it — otherwise a rejected configuration would already have started a process, and
// --check would have to start a server in order to report that it starts nothing.

import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { NOWHERE_URL, renderWrapper, runWrapper } from "./wrapper-harness.mjs";

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

test("--check reports the resolved configuration and starts nothing", () => {
  const r = run({ args: ["--check"], env: { HARNESS_IDENTITY: "probe-agent" } });
  assert.equal(r.launched, false, "--check must not launch the runtime");
  assert.equal(r.status, 0);
  const out = `${r.stdout}${r.stderr}`;
  assert.match(out, /codex/, "it must say which runtime it is");
  assert.match(out, /probe-agent/, "and which identity it resolved");
  assert.match(out, /127\.0\.0\.2:1/, "and which endpoint");
  assert.match(out, /\d+\.\d+\.\d+/, "and its own version");
});

test("--check resolves an identity given as a flag, in both spellings", () => {
  // The scan is a deliberate subset of the real parser. These two forms are what it covers, so they
  // are what it is held to — a check that reported <none> for a flag the launch honours would be
  // telling the operator the wrong thing about their own command.
  for (const args of [["--aify-agent", "flag-agent", "--check"], ["--aify-agent=flag-agent", "--check"]]) {
    const r = run({ args });
    assert.equal(r.launched, false);
    assert.match(`${r.stdout}`, /flag-agent/, `identity not resolved for ${JSON.stringify(args)}`);
  }
});

test("--check says plainly that this wrapper does not write MCP config", () => {
  const r = run({ args: ["--check"], env: { HARNESS_MCP_COMMAND: "node /custom/bridge.js" } });
  assert.match(`${r.stdout}`, /not used by this wrapper/, "an ignored input must be reported as ignored");
});

test("--check never starts the app-server", () => {
  // The whole reason the contract block sits above pick_port. If --check ran after it, every check
  // would allocate a port, spawn a background codex, and wait for it.
  const r = run({ args: ["--check"] });
  assert.equal(r.launched, false);
  assert.doesNotMatch(`${r.stdout}${r.stderr}`, /app-server at ws:/, "no server should be waited on");
});

test("HARNESS_ENDPOINT sets the service, and wins over the legacy name", () => {
  const r = run({ env: { HARNESS_ENDPOINT: "http://127.0.0.2:2/base", AIFY_COMMS_URL: NOWHERE_URL } });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_COMMS_URL, "http://127.0.0.2:2/base");
});

test("an explicitly empty HARNESS_ENDPOINT exits 78 without starting anything", () => {
  const r = run({ env: { HARNESS_ENDPOINT: "" } });
  assert.equal(r.launched, false);
  assert.equal(r.status, 78, `expected 78, got ${r.status}: ${r.stderr}`);
  assert.match(r.stderr, /HARNESS_ENDPOINT/);
});

test("HARNESS_IDENTITY and HARNESS_ROLE carry the agent's identity, flag still winning", () => {
  const env = run({ env: { HARNESS_IDENTITY: "harness-agent", HARNESS_ROLE: "architect" } });
  assert.equal(env.env.AIFY_AGENT_ID, "harness-agent");
  assert.equal(env.env.AIFY_AGENT_ROLE, "architect");

  const flag = run({
    args: ["--aify-agent", "flag-agent"],
    env: { HARNESS_IDENTITY: "harness-agent", AIFY_AGENT_ID: "legacy-agent" },
  });
  assert.equal(flag.env.AIFY_AGENT_ID, "flag-agent");
});

test("HARNESS_EXTRA_ENV exports host-supplied pairs verbatim", () => {
  const r = run({ env: { HARNESS_EXTRA_ENV: "FOO=bar\nBAZ=qux quux\nnot-a-pair\n" } });
  assert.equal(r.launched, true, `malformed entries must not be fatal: ${r.stderr}`);
  assert.equal(r.env.FOO, "bar");
  assert.equal(r.env.BAZ, "qux quux");
});
