// The daemon CLI the shell wrappers parse — and the field it must never print.
//
// The LAST entry on `every-module-is-imported-by-a-test.test.js`'s backlog. It was a bare script whose
// top level probed and spawned a daemon, so importing it did that; the gate's note said the answer was
// "an exported entry point or an end-to-end harness — a change to the module rather than to this list".
// This is the test for that change: `runHermesDaemonCli` takes its argv, its two daemon functions and
// its two writers, and returns the exit code instead of exiting, so nothing here starts a daemon.
//
// THE MOST IMPORTANT ASSERTION IS AN ABSENCE. `ensureDaemon` resolves an `api_server` key, and the
// module's docstring says twice that the key is deliberately NOT printed — because this line is
// captured into a shell variable by `hermes-aify` and would then appear in a process listing. A
// credential that leaks by being helpfully included is exactly the shape that does not announce itself,
// so the test hands the fake daemon a key and asserts it is nowhere in what was written.
//
// THE SECOND IS THE STREAM SPLIT. The wrapper parses stdout as JSON. Every diagnostic therefore has to
// go to stderr, and stdout has to carry EXACTLY ONE line — a usage message on stdout would be parsed as
// an endpoint, and a second line would make the parse ambiguous.

import assert from "node:assert/strict";
import test from "node:test";

import { runHermesDaemonCli } from "../hermes-daemon-cli.js";

const ENDPOINT = {
  host: "127.0.0.1",
  port: 8765,
  baseUrl: "http://127.0.0.1:8765",
  // What the CLI must not pass on. `ensureDaemon` really does resolve this.
  api_server: "hermes-api-key-do-not-print",
};

function run(argv, { ensure, stop } = {}) {
  const out = [];
  const err = [];
  return runHermesDaemonCli({
    argv: ["node", "hermes-daemon-cli.js", ...argv],
    ensure: ensure || (async ({ agentId }) => ({
      endpoint: ENDPOINT, started: true, version: "0.15.1", agentId,
    })),
    stop: stop || (async ({ agentId }) => ({ stopped: true, pid: 4242, agentId })),
    stdout: (text) => out.push(text),
    stderr: (text) => err.push(text),
  }).then((code) => ({ code, out: out.join(""), err: err.join("") }));
}

// ── the credential that must not be printed ─────────────────────────────────────────────────────

test("the api_server KEY IS NOT PRINTED, even though the daemon resolved one", async () => {
  const { out, err } = await run(["sc-hermes"]);
  assert.ok(!out.includes("hermes-api-key-do-not-print"), out);
  assert.ok(!err.includes("hermes-api-key-do-not-print"), err);
  assert.ok(!("api_server" in JSON.parse(out.trim())), out);
});

test("the printed keys are EXACTLY the six the wrapper reads", async () => {
  // A census rather than a spot check: this is the one place a new field would be added by someone
  // adding it to the endpoint, and an allow-list is the only shape that notices.
  const { out } = await run(["sc-hermes"]);
  assert.deepEqual(Object.keys(JSON.parse(out.trim())).sort(),
    ["agentId", "baseUrl", "host", "port", "started", "version"]);
});

test("the endpoint values themselves are passed through", async () => {
  const { code, out } = await run(["sc-hermes"]);
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(out.trim()), {
    agentId: "sc-hermes",
    host: "127.0.0.1",
    port: 8765,
    baseUrl: "http://127.0.0.1:8765",
    started: true,
    version: "0.15.1",
  });
});

test("STARTED is a real boolean, not whatever the daemon returned", async () => {
  // The wrapper branches on it. A truthy string or a number would still be truthy in bash but is not
  // what a JSON consumer checks for.
  const { out } = await run(["sc-hermes"], {
    ensure: async () => ({ endpoint: ENDPOINT, started: "yes", version: "0.15.1" }),
  });
  assert.strictEqual(JSON.parse(out.trim()).started, true);
});

test("a daemon that returns NO endpoint still prints a parseable line", async () => {
  // `result.endpoint || {}`. Without the fallback this throws, and the wrapper sees an empty stdout
  // plus a stack trace — indistinguishable from the daemon never coming up.
  const { code, out } = await run(["sc-hermes"], {
    ensure: async () => ({ started: false }),
  });
  assert.equal(code, 0);
  const parsed = JSON.parse(out.trim());
  assert.equal(parsed.agentId, "sc-hermes");
  assert.equal(parsed.started, false);
});

// ── the stream split ────────────────────────────────────────────────────────────────────────────

test("stdout carries EXACTLY ONE line", async () => {
  const { out } = await run(["sc-hermes"]);
  assert.equal(out.split("\n").filter(Boolean).length, 1, JSON.stringify(out));
  assert.ok(out.endsWith("\n"), "the wrapper reads a line — it must be terminated");
});

test("a MISSING agentId is a usage error on STDERR with exit 2", async () => {
  // On stdout it would be parsed as an endpoint. Exit 2 rather than 1 distinguishes "you called me
  // wrong" from "the daemon would not come up".
  const { code, out, err } = await run([]);
  assert.equal(code, 2);
  assert.equal(out, "");
  assert.match(err, /missing <agentId> argument/);
  assert.match(err, /usage: node hermes-daemon-cli\.js <agentId>/);
});

test("a WHITESPACE agentId is missing too", async () => {
  const { code, err } = await run(["   "]);
  assert.equal(code, 2);
  assert.match(err, /missing <agentId>/);
});

test("a THROWN daemon error goes to stderr with exit 1 and nothing on stdout", async () => {
  // The LOUD failure the docstring promises. Anything on stdout here would be parsed as an endpoint by
  // a wrapper that had just been told the daemon failed.
  const { code, out, err } = await run(["sc-hermes"], {
    ensure: async () => { throw new Error("hermes gateway refused to bind :8765"); },
  });
  assert.equal(code, 1);
  assert.equal(out, "");
  assert.match(err, /^\[hermes-daemon-cli\] hermes gateway refused to bind :8765\n$/);
});

test("a non-Error rejection still produces a prefixed line", async () => {
  const { code, err } = await run(["sc-hermes"], {
    ensure: async () => { throw "just a string"; },
  });
  assert.equal(code, 1);
  assert.match(err, /^\[hermes-daemon-cli\] just a string\n$/);
});

// ── the stop subcommand ─────────────────────────────────────────────────────────────────────────

test("stop tears the daemon down and reports what it did", async () => {
  const { code, out } = await run(["stop", "sc-hermes"]);
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(out.trim()), { agentId: "sc-hermes", stopped: true, pid: 4242 });
});

test("stop is recognised case-insensitively", async () => {
  // Wrappers are written by hand in two shells. `STOP` reaching the run path would PROBE AND SPAWN a
  // daemon the operator asked to tear down.
  for (const word of ["stop", "STOP", "Stop", "  stop  "]) {
    const seen = [];
    const { code } = await run([word, "sc-hermes"], {
      ensure: async () => { seen.push("ensure"); return { endpoint: ENDPOINT }; },
      stop: async () => { seen.push("stop"); return { stopped: true, pid: 1 }; },
    });
    assert.equal(code, 0, word);
    assert.deepEqual(seen, ["stop"], `${word} took the wrong path`);
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
  // The dangerous default. `stopDaemon` keyed on an empty agent id is at best a no-op and at worst a
  // match on something else, so the CLI refuses before calling it.
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

// ── the entry point ─────────────────────────────────────────────────────────────────────────────

test("the module is still an executable script", async () => {
  // `install.sh` invokes it as `node hermes-daemon-cli.js <agentId>` from two wrappers, and verifies it
  // with `node --check`. A split that exported the function and dropped the tail would leave both
  // wrappers silently doing nothing.
  const { readFileSync } = await import("node:fs");
  const path = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const here = path.dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(path.join(here, "..", "hermes-daemon-cli.js"), "utf-8");
  assert.match(source, /^#!\/usr\/bin\/env node/);
  assert.match(source, /if \(isEntryPoint\(\)\)/);
  assert.match(source, /process\.exit\(await runHermesDaemonCli\(\)\)/);
});

test("IMPORTING it probes no daemon and prints nothing", async () => {
  // Observed from a CHILD: this file imported the module before any case ran, so nothing in-process can
  // witness its own import. Before the split, an import reached out to a real hermes gateway.
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
