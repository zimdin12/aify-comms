// Importing `server.js` must not start a bridge.
//
// THIS IS THE STRUCTURAL REASON server.js has 27 never-called functions out of 27 — the entire file,
// untested, and the largest single block in the coverage census. It was not neglect: until v0.6 Phase 1
// the module ran its boot block at IMPORT time. Four loops started, and `ensureEnvironmentHeartbeat`
// REGISTERED the importing process as the environment bridge — superseding the live one and reaping its
// managed workers.
//
// That is not a hypothetical. It is the documented reason for the standing rule "never run a bare
// `aify-comms`", and it is what took the whole managed fleet down on 2026-08-11 from a four-second run
// meant only to confirm the launcher still started. A test suite that imported this module would have
// done the same thing, every run.
//
// So the boot block moved under the `__isEntrypoint` guard that already gated `main()`. This test is the
// receipt: it imports the module as a NON-entrypoint and requires that nothing booted.
//
// THE ENVIRONMENT IS SEALED HOSTILE, and "hostile" means SET-BUT-POINTING-NOWHERE (127.0.0.2:1), never
// unset and never pointing at a real service. An unset URL would let the module fall back to
// `defaultFallbackServerUrls`, which adds the operator's real 127.0.0.1:8800 — the exact path by which
// an earlier test in this repo registered six agents into the production registry.
//
// ROLE FLAGS ARE STRIPPED rather than set falsy: `AIFY_ENVIRONMENT_BRIDGE=1` once turned a test run into
// the environment bridge and reaped seven live gateway hosts.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// A `file://` URL, not a bare Windows path: `import("C:\...")` is rejected by the ESM
// loader ("Only URLs with a scheme in: file, data, and node are supported").
const SERVER_URL_SPEC = pathToFileURL(path.join(STDIO, "server.js")).href;

/**
 * Import server.js in a CHILD process as a non-entrypoint, and report what it did.
 *
 * A child process, because the only honest way to ask "does importing this start timers" is to import
 * it somewhere disposable and look. Doing it in-process would leave whatever it started running inside
 * the test runner for the rest of the suite.
 */
function importServerAndReport() {
  const script = `
    import { fileURLToPath } from "node:url";

    // Count what the import starts. Both are restored before reporting so nothing here depends on the
    // module having left them alone.
    const realSetInterval = globalThis.setInterval;
    const realFetch = globalThis.fetch;
    let intervals = 0;
    const requests = [];
    globalThis.setInterval = (...args) => { intervals += 1; return realSetInterval(() => {}, 1 << 30); };
    globalThis.fetch = async (url) => { requests.push(String(url)); throw new Error("no network in this test"); };

    let importError = null;
    try {
      await import(${JSON.stringify(SERVER_URL_SPEC)});
    } catch (error) {
      importError = String(error?.message || error);
    }

    // Give anything the import scheduled a chance to actually fire before we report.
    await new Promise((resolve) => realSetInterval(resolve, 50));

    globalThis.setInterval = realSetInterval;
    globalThis.fetch = realFetch;
    process.stdout.write(JSON.stringify({ intervals, requests, importError }));
    process.exit(0);
  `;
  const out = execFileSync(process.execPath, ["--input-type=module", "-e", script], {
    cwd: STDIO,
    env: {
      ...sealedChildEnv(),
      // SET, and pointing nowhere. See the header.
      AIFY_SERVER_URL: "http://127.0.0.2:1",
      CLAUDE_MCP_SERVER_URL: "http://127.0.0.2:1",
      AIFY_SERVER_FALLBACK_URLS: "http://127.0.0.2:1",
      CLAUDE_MCP_FALLBACK_URLS: "http://127.0.0.2:1",
    },
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 60_000,
  });
  return JSON.parse(out);
}

test("importing server.js starts NO timers", () => {
  const report = importServerAndReport();
  assert.equal(report.importError, null, `the import itself failed: ${report.importError}`);
  assert.equal(
    report.intervals, 0,
    `importing server.js started ${report.intervals} timer(s). Before v0.6 Phase 1 it started four — `
    + "the environment control loop, the usage collector, the environment heartbeat and the terminal "
    + "control loop — which is why nothing in this file could ever be imported by a test.",
  );
});

test("importing server.js REGISTERS NOTHING — the property the fleet outage was about", () => {
  const report = importServerAndReport();
  const registrations = report.requests.filter((url) => /\/environments|\/agents|\/heartbeat/.test(url));
  assert.deepEqual(
    registrations, [],
    "importing server.js called the service. `ensureEnvironmentHeartbeat` registers the process as the "
    + "environment bridge, which SUPERSEDES the live one and reaps its managed workers — the 2026-08-11 "
    + `fleet outage, from a four-second run. Calls seen: ${JSON.stringify(report.requests)}`,
  );
});

test("NOTHING starts the environment bridge, because the code that could is not here", () => {
  // v0.6.2 REPLACED this test's subject. It used to require TWO `if (__isEntrypoint)` blocks -- the
  // boot block and main() -- to prove the boot block had not grown a second condition. The boot block
  // was deleted with the bridge, so counting guards now measures nothing: one is the honest number and
  // a floor of one passes on a file that gates nothing at all.
  //
  // So it asks the question the guard was standing in for. The two tests above prove an import starts
  // no timers and calls nothing; this one proves the SOURCE cannot, by requiring the bridge's four
  // starters to be absent as CODE. A mention in a comment is not a call, so the scan reads statements:
  // the header comment above deliberately names `ensureEnvironmentHeartbeat`, and a test that fired on
  // that would be unfixable except by deleting the history.
  const source = readFileSync(path.join(STDIO, "server.js"), "utf8");
  const code = source
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  const STARTERS = [
    "ensureEnvironmentHeartbeat",
    "bootstrapEnvironmentBridge",
    "ensureSpawnLoop",
    "ensureEnvironmentControlLoop",
    "ensureUsageCollector",
  ];
  const present = STARTERS.filter((name) => new RegExp(`\\b${name}\\b`).test(code));
  assert.deepEqual(
    present, [],
    `server.js names ${present.join(", ")} as code. Each of these REGISTERED THIS PROCESS AS THE `
    + "ENVIRONMENT BRIDGE or started one of its loops, and aify-env is the host tier now -- a second "
    + "bridge on this host supersedes the live one and reaps its managed workers.",
  );

  // POSITIVE CONTROL, in the same run: the scan must find a name that IS there, or its empty answer
  // above is the instrument being broken rather than the bridge being gone.
  assert.ok(
    /\bensureDispatchLoop\b/.test(code),
    "the scan cannot see `ensureDispatchLoop`, which server.js does call -- so its zero above is not "
    + "evidence of anything",
  );
  // NEGATIVE CONTROL, on a SYNTHETIC fixture rather than on server.js: the stripper must be able to
  // say ABSENT for a name that appears only in prose. Its first version read a real comment in
  // server.js, and rewriting that comment -- in the same change -- took the fixture with it, so the
  // control failed for a reason that had nothing to do with the property. A control that a nearby edit
  // can silently disarm is not one.
  const proseOnly = "// ensureEnvironmentHeartbeat();\n/* ensureSpawnLoop(); */\nconst x = 1;\n";
  const strippedProse = proseOnly
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(
    !/ensureEnvironmentHeartbeat|ensureSpawnLoop/.test(strippedProse),
    "the comment stripper leaves commented-out calls behind, so the scan above would fire on history "
    + "rather than on code -- and the only way to quiet it would be to delete the incident this file "
    + "is written to preserve",
  );
});
