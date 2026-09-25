// The kill-prior CLI the hermes-aify launchers call: `node hermes-daemon-cli.js stop <agentId>`.
//
// `runHermesDaemonCli` takes its argv, the stop function and its two writers, and returns the exit code
// instead of exiting, so nothing here stops a real gateway.
//
// THE STREAM SPLIT. A launcher may parse stdout as JSON, so every diagnostic goes to stderr and stdout
// carries exactly one line.

import assert from "node:assert/strict";
import test from "node:test";

import { runHermesDaemonCli } from "../hermes-daemon-cli.js";

function run(argv, { stop, env } = {}) {
  const out = [];
  const err = [];
  return runHermesDaemonCli({
    argv: ["node", "hermes-daemon-cli.js", ...argv],
    stop: stop || (async ({ agentId }) => ({ stopped: true, pid: 4242, agentId })),
    stdout: (text) => out.push(text),
    stderr: (text) => err.push(text),
    env: env || {},
  }).then((code) => ({ code, out: out.join(""), err: err.join("") }));
}

test("stop tears the daemon down and reports what it did, on exactly one stdout line", async () => {
  const { code, out } = await run(["stop", "sc-hermes"]);
  assert.equal(code, 0);
  assert.equal(out.split("\n").filter(Boolean).length, 1, JSON.stringify(out));
  assert.ok(out.endsWith("\n"), "the wrapper reads a line — it must be terminated");
  assert.deepEqual(JSON.parse(out.trim()), { agentId: "sc-hermes", stopped: true, pid: 4242 });
});

test("stop is recognised case-insensitively", async () => {
  for (const word of ["stop", "STOP", "Stop", "  stop  "]) {
    let called = 0;
    const { code } = await run([word, "sc-hermes"], {
      stop: async () => { called += 1; return { stopped: true, pid: 1 }; },
    });
    assert.equal(code, 0, word);
    assert.equal(called, 1, `${word} did not reach stopDaemon`);
  }
});

test("stop EXITS 0 even when nothing was running", async () => {
  // Wrappers call it unconditionally on relaunch. A non-zero exit for "there was nothing to stop"
  // would fail every first launch.
  const { code, out } = await run(["stop", "sc-hermes"], {
    stop: async () => ({ stopped: false, pid: null }),
  });
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(out.trim()), { agentId: "sc-hermes", stopped: false, pid: null });
});

test("stop with NO agentId is a usage error, not a fleet-wide teardown", async () => {
  let called = 0;
  const { code, out, err } = await run(["stop"], {
    stop: async () => { called += 1; return { stopped: true }; },
  });
  assert.equal(code, 2);
  assert.equal(called, 0, "stopDaemon was called with no agent id");
  assert.equal(out, "");
  assert.match(err, /missing <agentId> for stop/);
});

test("stop's STOPPED flag is a real boolean", async () => {
  const { out } = await run(["stop", "sc-hermes"], {
    stop: async () => ({ stopped: "yes", pid: 7 }),
  });
  assert.strictEqual(JSON.parse(out.trim()).stopped, true);
});

test("the prior reap runs only for a launcher holding the agent lease", async () => {
  const seen = [];
  const stop = async (opts) => { seen.push(opts.reapPrior); return { stopped: false }; };
  await run(["stop", "sc-hermes"], { stop, env: { AIFY_AGENT_LEASE: "4242" } });
  await run(["stop", "sc-hermes"], { stop, env: {} });
  assert.deepEqual(seen, [true, false]);
});

test("the retired ensure form is a usage error that starts nothing", async () => {
  // `node hermes-daemon-cli.js <agentId>` used to probe and spawn an api_server daemon. It must not
  // silently fall through to stop either: a word that is not `stop` does nothing.
  let called = 0;
  for (const argv of [["sc-hermes"], []]) {
    const { code, out, err } = await run(argv, {
      stop: async () => { called += 1; return { stopped: true }; },
    });
    assert.equal(code, 2, JSON.stringify(argv));
    assert.equal(out, "");
    assert.match(err, /usage: node hermes-daemon-cli\.js stop <agentId>/);
  }
  assert.equal(called, 0);
});

test("a THROWN stop error goes to stderr with exit 1 and nothing on stdout", async () => {
  const { code, out, err } = await run(["stop", "sc-hermes"], {
    stop: async () => { throw new Error("refused"); },
  });
  assert.equal(code, 1);
  assert.equal(out, "");
  assert.match(err, /^\[hermes-daemon-cli\] refused\n$/);
});

test("the module is still an executable script", async () => {
  // Both launchers invoke it as a script, and install.sh verifies it with `node --check`.
  const { readFileSync } = await import("node:fs");
  const path = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const here = path.dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(path.join(here, "..", "hermes-daemon-cli.js"), "utf-8");
  assert.match(source, /^#!\/usr\/bin\/env node/);
  assert.match(source, /if \(isEntryPoint\(\)\)/);
  assert.match(source, /process\.exit\(await runHermesDaemonCli\(\)\)/);
});

test("IMPORTING it stops nothing and prints nothing", async () => {
  // Observed from a CHILD: this file imported the module before any case ran, so nothing in-process can
  // witness its own import.
  const { spawnSync } = await import("node:child_process");
  const path = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const here = path.dirname(fileURLToPath(import.meta.url));
  const child = spawnSync(process.execPath,
    ["-e", "import('../hermes-daemon-cli.js').then(m => { if (!m.runHermesDaemonCli) process.exit(3); })"],
    { cwd: here, encoding: "utf-8", timeout: 20_000 });
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stdout, "", `importing printed: ${JSON.stringify(child.stdout)}`);
});
