// Which STREAM each CLI writes to, exercised through its real default writers.
//
// Nineteenth cluster off the V8-coverage census: the `out`/`err`/`stdout`/`stderr` default parameters of
// `runResolveSessionCli` (hermes-active-session.mjs), `runHermesDaemonCli` (hermes-daemon-cli.js) and
// `runEnsureHostCli` (hermes-managed-host.js), plus `cliUsage` in runtime-markers.js. Every existing test
// injects its own writers - which is the whole point of the seam - so the defaults, the thing that actually
// runs in production, had a zero call count.
//
// THE CONTRACT IS THE STREAM, and it is load-bearing. Each of these CLIs is read by a WRAPPER that parses ONE
// JSON line (or one bare id) from stdout; `hermes-daemon-cli.js` says so in a comment at the write. Diagnostics
// and usage therefore have to go to stderr. A default writer pointing at the wrong stream does not look like a
// stream bug: the wrapper's JSON.parse fails on usage text, and the operator sees "the daemon failed to start"
// for a launch that was only ever missing an argument.
//
// EVERY CASE RUNS IN A CHILD PROCESS, because that is the only way to observe the defaults - they write to the
// real process streams. The child imports the module by absolute URL and calls the function with its NON-writer
// deps injected, so nothing real is ensured, stopped or spawned.
//
// SAFETY: only refusal paths and one faked stop are exercised. `runEnsureHostCli` and `runHermesDaemonCli`
// would otherwise bring up a gateway host or a daemon on the operator's machine; each refuses before any side
// effect when its agentId is missing, and that refusal is what gets measured.
//
// ONE ENTRY STAYS OPEN, deliberately: `runEnsureHostCli`'s `out` default. It is only called on SUCCESS, after
// the gateway host is up, so pointing it at the wrong stream is invisible to every path reachable without
// spawning a real host on the operator's machine — a mutation swapping it survives this file. Its sibling
// (`err`) and the refusal are covered here; the success-path writer needs a harness that can stand up a fake
// gateway host, which is its own slice.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

import { tmpDir } from "./_tmpdir.js";
import { leakedCarriers, sealedChildEnv } from "./_child-env.mjs";

const HERE = new URL(".", import.meta.url);
const moduleUrl = (name) => JSON.stringify(new URL(`../${name}`, HERE).href);

// Runs a snippet in a fresh node process and returns its streams SEPARATELY. Nothing is merged: the whole
// question here is which stream a byte landed on.
async function runChild(source, extra = {}) {
  const dir = tmpDir("aify-cli-stream-");
  const file = path.join(dir, "case.mjs");
  await fs.writeFile(file, source, "utf8");
  // TAKES THE ENV MAP DIRECTLY. It used to take `{ env }`, and the resolve-session case below passed the map
  // itself — so the destructure found no `env` key, overrode nothing, and the child inherited a live wrapper's
  // gateway URL and active-session file. It passed here (those are unset on this machine) and failed in a
  // reviewer's live environment. The shape that allowed the mistake is gone, and the seal is now asserted.
  const env = sealedChildEnv(extra);
  const leaked = leakedCarriers(env).filter((name) => !(name in extra));
  assert.deepEqual(leaked, [], `these live carriers would reach the child: ${leaked.join(", ")}`);
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [file], {
      stdio: ["ignore", "pipe", "pipe"],
      env,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    const bail = setTimeout(() => { child.kill(); reject(new Error("the child never exited")); }, 20000);
    child.on("error", reject);
    child.on("exit", (code) => { clearTimeout(bail); resolve({ code, stdout, stderr }); });
  });
}

// ── the daemon CLI ──────────────────────────────────────────────────────────

test("the daemon CLI's usage goes to stderr, and stdout stays clean", async () => {
  // Exit 2 with usage on stderr. Usage on stdout would reach the wrapper's JSON.parse instead.
  const { code, stdout, stderr } = await runChild([
    `import { runHermesDaemonCli } from ${moduleUrl("hermes-daemon-cli.js")};`,
    // No writers injected: the defaults are the subject. `ensure`/`stop` are, so no daemon can start.
    "const rc = await runHermesDaemonCli({",
    '  argv: ["node", "cli"],',
    '  ensure: async () => { throw new Error("must not ensure a daemon"); },',
    '  stop: async () => { throw new Error("must not stop a daemon"); },',
    "});",
    "process.exit(rc);",
  ].join("\n"));

  assert.equal(code, 2, "the missing-argument exit code changed");
  assert.match(stderr, /missing <agentId>/);
  assert.equal(stdout, "", `usage text reached stdout: ${JSON.stringify(stdout)}`);
});

test("the daemon CLI's stop result goes to STDOUT as one JSON line", async () => {
  // The wrapper parses this. Sending it to stderr would leave the wrapper with an empty stdout and no way to
  // tell a stopped daemon from a crashed CLI.
  const { code, stdout, stderr } = await runChild([
    `import { runHermesDaemonCli } from ${moduleUrl("hermes-daemon-cli.js")};`,
    "const rc = await runHermesDaemonCli({",
    '  argv: ["node", "cli", "stop", "agent-x"],',
    '  ensure: async () => { throw new Error("must not ensure a daemon"); },',
    "  stop: async () => ({ stopped: true, pid: 4242 }),",
    "});",
    "process.exit(rc);",
  ].join("\n"));

  assert.equal(code, 0);
  // Byte-exact, INCLUDING the trailing newline. A line-oriented reader on the other end either blocks waiting
  // for the terminator or concatenates this with whatever is printed next; `stdout.trim()` in the assertion
  // hides both, which is how the dropped-newline mutation first survived this file.
  assert.equal(stdout, `${JSON.stringify({ agentId: "agent-x", stopped: true, pid: 4242 })}\n`);
  const lines = stdout.split("\n").filter(Boolean);
  assert.equal(lines.length, 1, `expected exactly one line on stdout, got ${JSON.stringify(stdout)}`);
  assert.equal(stderr, "", `diagnostics leaked onto stderr for a clean stop: ${JSON.stringify(stderr)}`);
});

test("the daemon CLI's stop usage also goes to stderr", async () => {
  const { code, stdout, stderr } = await runChild([
    `import { runHermesDaemonCli } from ${moduleUrl("hermes-daemon-cli.js")};`,
    "const rc = await runHermesDaemonCli({",
    '  argv: ["node", "cli", "stop"],',
    '  ensure: async () => { throw new Error("must not ensure a daemon"); },',
    '  stop: async () => { throw new Error("must not stop a daemon"); },',
    "});",
    "process.exit(rc);",
  ].join("\n"));

  assert.notEqual(code, 0, "a missing stop target exited successfully");
  assert.match(stderr, /missing <agentId>/);
  assert.equal(stdout, "");
});

// ── the resolve-session CLI ─────────────────────────────────────────────────

test("the resolved session id is the ONLY thing on stdout", async () => {
  // The wrapper reads this bare id. An explicit resume with NO gateway is the path that resolves without a
  // network: the id is authoritative, so the active_list query is skipped entirely and the marker is seeded.
  //
  // THE GATEWAY ENV IS SEALED EMPTY IN THE CHILD, and the child asserts that before calling. `wsUrl` is read
  // from AIFY_HERMES_GATEWAY_URL / HERMES_TUI_GATEWAY_URL, so an unsealed run on the operator's machine would
  // take the gateway branch and query a LIVE hermes — a test of mine has read a live marker before, which is
  // why this is asserted rather than assumed.
  const { code, stdout, stderr } = await runChild([
    `import { runResolveSessionCli } from ${moduleUrl("hermes-active-session.mjs")};`,
    'for (const key of ["AIFY_HERMES_GATEWAY_URL", "HERMES_TUI_GATEWAY_URL", "AIFY_HERMES_ACTIVE_SESSION_FILE"]) {',
    '  if (String(process.env[key] || "").trim()) { process.stderr.write("seal failed: " + key); process.exit(4); }',
    "}",
    "const result = await runResolveSessionCli('agent-y', {",
    "  explicitId: 'sess-explicit-123',",
    "  tempDir: process.env.AIFY_TEST_TMP,",
    "  activeSessionFile: '',",
    "});",
    "if (result.resolved !== 'sess-explicit-123') { process.stderr.write('unexpected: ' + result.resolved); process.exit(3); }",
    "process.exit(0);",
  ].join("\n"), {
    AIFY_TEST_TMP: tmpDir("aify-cli-stream-marker-"),
    AIFY_HERMES_GATEWAY_URL: "",
    HERMES_TUI_GATEWAY_URL: "",
    AIFY_HERMES_ACTIVE_SESSION_FILE: "",
  });

  assert.equal(code, 0, `the CLI did not resolve the explicit id: ${stderr}`);
  assert.equal(stdout, "sess-explicit-123\n",
    "stdout is not exactly the resolved id plus a newline — the wrapper reads this verbatim");
  // And the reasoning goes to stderr, where it does not disturb the id the wrapper is reading. This one is
  // worth having rather than silence: "which branch resolved this" is the first question when an agent resumes
  // the wrong session, and the answer is otherwise unrecoverable after the fact.
  assert.match(stderr, /resolve-session: agent 'agent-y' → sess-explicit-123/);
  assert.match(stderr, /explicit-resume/, "the stderr line does not say WHICH branch resolved the id");
});

test("the resolve-session CLI refuses a missing agent id without printing to stdout", async () => {
  const { code, stdout, stderr } = await runChild([
    `import { runResolveSessionCli } from ${moduleUrl("hermes-active-session.mjs")};`,
    "try {",
    "  await runResolveSessionCli('', {});",
    "  process.stdout.write('RESOLVED-ANYWAY');",
    "  process.exit(0);",
    "} catch (error) {",
    "  process.stderr.write(String(error && error.message));",
    "  process.exit(7);",
    "}",
  ].join("\n"));

  assert.equal(code, 7, "a missing agent id did not reach the refusal");
  assert.match(stderr, /requires an agentId/);
  assert.equal(stdout, "", "something was printed for a call that refused");
});

// ── the ensure-host CLI ─────────────────────────────────────────────────────

test("ensure-host refuses a missing agent id before it can bring a host up", async () => {
  // The refusal has to come first: resolving a port or spawning a gateway for a nameless agent would leave a
  // process nothing owns. Its default writers are in place and must stay silent on stdout.
  const { code, stdout, stderr } = await runChild([
    `import { runEnsureHostCli } from ${moduleUrl("hermes-managed-host.js")};`,
    "try {",
    "  await runEnsureHostCli('', {",
    '    spawnImpl: () => { throw new Error("must not spawn a gateway host"); },',
    '    fetchImpl: async () => { throw new Error("must not probe a gateway"); },',
    "  });",
    "  process.stdout.write('ENSURED-ANYWAY');",
    "  process.exit(0);",
    "} catch (error) {",
    "  process.stderr.write(String(error && error.message));",
    "  process.exit(7);",
    "}",
  ].join("\n"));

  assert.equal(code, 7, "ensure-host did not refuse a nameless agent");
  assert.match(stderr, /requires an agentId/);
  assert.equal(stdout, "", "ensure-host printed coordinates for an agent that has no id");
});

// ── the runtime-markers script ──────────────────────────────────────────────

test("the runtime-markers script prints its usage to stderr and exits 1", async () => {
  // `cliUsage` is reachable only by running the file as a script with missing or unknown arguments. Its four
  // real subcommands each write a path or a JSON blob to stdout, so usage sharing that stream would be
  // indistinguishable from a result.
  const markers = new URL("../runtime-markers.js", HERE);
  for (const args of [[], ["write"], ["write", "claude"], ["bogus-command", "claude", "C:/tmp"]]) {
    const { code, stdout, stderr } = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [markers.pathname.replace(/^\//, ""), ...args], {
        stdio: ["ignore", "pipe", "pipe"],
      });
      let out = "";
      let err = "";
      child.stdout.on("data", (c) => { out += String(c); });
      child.stderr.on("data", (c) => { err += String(c); });
      child.on("error", reject);
      child.on("exit", (c) => resolve({ code: c, stdout: out, stderr: err }));
    });

    assert.equal(code, 1, `${JSON.stringify(args)}: expected exit 1`);
    assert.match(stderr, /Usage: runtime-markers\.js <write\|remove\|path\|read>/,
      `${JSON.stringify(args)}: usage text missing from stderr`);
    assert.equal(stdout, "", `${JSON.stringify(args)}: usage reached stdout`);
  }
});
