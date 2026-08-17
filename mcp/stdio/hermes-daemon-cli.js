#!/usr/bin/env node
// Tiny CLI shim around ensureDaemon — lets the shell wrappers (install.sh
// hermes-aify) bring up an agent's per-agent `hermes gateway run` daemon
// without re-implementing the probe/spawn/poll loop in bash/PowerShell.
//
// Usage:  node hermes-daemon-cli.js <agentId>
//         node hermes-daemon-cli.js stop <agentId>   (tear down the daemon)
//
// On success: prints ONE JSON line with the resolved endpoint to stdout and
//   exits 0, e.g. {"agentId":"sc-hermes","host":"127.0.0.1","port":8765,
//   "baseUrl":"http://127.0.0.1:8765","started":true,"version":"0.15.1"}
//   (the api_server key is intentionally NOT printed — the wrapper resolves it
//   itself via hermes-endpoint.js / the sidecar does, so it never lands in a
//   shell variable or process listing).
// On failure: prints the LOUD ensureDaemon error to stderr and exits non-zero.
//
// Contract: docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.
//
// SPLIT INTO A FUNCTION AND AN ENTRY POINT, 2026-08-17. It was a bare script, so importing it RAN a
// daemon probe/spawn — which is why `every-module-is-imported-by-a-test.test.js` recorded it as one of
// two modules that could not be import-tested, and why its note said the answer was "an exported entry
// point or an end-to-end harness — a change to the module rather than to this list". `runHermesDaemonCli`
// takes its argv, its two daemon functions and its two writers, and RETURNS the exit code instead of
// calling `process.exit` itself; the tail below is the only place that exits. Every byte written to
// stdout and stderr is unchanged, which is what `install.sh` and the PowerShell wrapper parse.

import path from "node:path";
import { fileURLToPath } from "node:url";

import { ensureDaemon, stopDaemon } from "./hermes-daemon.js";

const USAGE_RUN =
  "[hermes-daemon-cli] FATAL: missing <agentId> argument.\n" +
  "  usage: node hermes-daemon-cli.js <agentId>\n";
const USAGE_STOP =
  "[hermes-daemon-cli] FATAL: missing <agentId> for stop.\n" +
  "  usage: node hermes-daemon-cli.js stop <agentId>\n";

// Tear down the per-agent daemon: `node hermes-daemon-cli.js stop <agentId>`.
// Best-effort (stopDaemon never throws); prints the result JSON and exits 0 so
// shell wrappers can call it unconditionally on relaunch/teardown.
async function runStop(agentId, { stop, stdout, stderr }) {
  if (!agentId) {
    stderr(USAGE_STOP);
    return 2;
  }
  const result = await stop({ agentId });
  stdout(JSON.stringify({ agentId, stopped: !!result.stopped, pid: result.pid }) + "\n");
  return 0;
}

export async function runHermesDaemonCli({
  argv = process.argv,
  ensure = ensureDaemon,
  stop = stopDaemon,
  stdout = (text) => process.stdout.write(text),
  stderr = (text) => process.stderr.write(text),
} = {}) {
  try {
    // Subcommand form: `stop <agentId>`.
    if (String(argv[2] || "").trim().toLowerCase() === "stop") {
      return await runStop(String(argv[3] || "").trim(), { stop, stdout, stderr });
    }

    const agentId = String(argv[2] || "").trim();
    if (!agentId) {
      stderr(USAGE_RUN);
      return 2;
    }

    const result = await ensure({ agentId });
    const endpoint = result.endpoint || {};
    // One JSON line to stdout (the wrapper parses this). Key omitted on purpose: `api_server` would
    // land in a shell variable and a process listing, which is the one thing this shape prevents.
    stdout(
      JSON.stringify({
        agentId,
        host: endpoint.host,
        port: endpoint.port,
        baseUrl: endpoint.baseUrl,
        started: !!result.started,
        version: result.version,
      }) + "\n",
    );
    return 0;
  } catch (error) {
    stderr(`[hermes-daemon-cli] ${error?.message || String(error)}\n`);
    return 1;
  }
}

function isEntryPoint() {
  const invoked = process.argv[1];
  if (!invoked) return false;
  try {
    return path.resolve(invoked) === path.resolve(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isEntryPoint()) {
  process.exit(await runHermesDaemonCli());
}
