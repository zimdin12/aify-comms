#!/usr/bin/env node
// Tiny CLI shim around stopDaemon — the kill-prior step the hermes-aify launchers run before they
// start a new gateway, so the shell does not re-implement the port and pid checks.
//
// Usage:  node hermes-daemon-cli.js stop <agentId>
//
// Prints ONE JSON line ({agentId, stopped, pid}) to stdout and exits 0, so a wrapper can call it
// unconditionally. Anything else is a usage error on stderr with exit 2. The `<agentId>` form that
// ensured an api_server daemon was retired with that daemon on 2026-06-02; the launchers call only
// `stop` (aify-wrapper `hermes-aify.sh.in` and the PowerShell launcher install.sh renders).
//
// A FUNCTION AND AN ENTRY POINT, so importing it does nothing. `runHermesDaemonCli` takes its argv, the
// stop function and its two writers, and RETURNS the exit code; the tail below is the only place that
// exits.

import path from "node:path";
import { fileURLToPath } from "node:url";

import { stopDaemon } from "./hermes-daemon.js";

const USAGE =
  "[hermes-daemon-cli] FATAL: the only command is stop.\n" +
  "  usage: node hermes-daemon-cli.js stop <agentId>\n";
const USAGE_STOP =
  "[hermes-daemon-cli] FATAL: missing <agentId> for stop.\n" +
  "  usage: node hermes-daemon-cli.js stop <agentId>\n";

// Tear down the per-agent daemon: `node hermes-daemon-cli.js stop <agentId>`.
// Best-effort (stopDaemon never throws); prints the result JSON and exits 0 so
// shell wrappers can call it unconditionally on relaunch/teardown.
//
// THE PRIOR REAP RUNS ONLY FOR A LAUNCHER THAT HOLDS THE AGENT LEASE (AIFY_AGENT_LEASE, exported by
// aify-wrapper's claim). That claim is what established that no live instance of this agent is running
// -- or that this start was explicit and has already replaced it. Without it (an older launcher, a helper
// that failed), a leftover-looking gateway may belong to a live instance, and an automatic start must
// not end one.
async function runStop(agentId, { stop, stdout, stderr, env }) {
  if (!agentId) {
    stderr(USAGE_STOP);
    return 2;
  }
  const holdsLease = Number(env?.AIFY_AGENT_LEASE) > 0;
  const result = await stop({ agentId, reapPrior: holdsLease });
  stdout(JSON.stringify({ agentId, stopped: !!result.stopped, pid: result.pid }) + "\n");
  return 0;
}

export async function runHermesDaemonCli({
  argv = process.argv,
  stop = stopDaemon,
  stdout = (text) => process.stdout.write(text),
  stderr = (text) => process.stderr.write(text),
  env = process.env,
} = {}) {
  if (String(argv[2] || "").trim().toLowerCase() !== "stop") {
    stderr(USAGE);
    return 2;
  }
  try {
    return await runStop(String(argv[3] || "").trim(), { stop, stdout, stderr, env });
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
