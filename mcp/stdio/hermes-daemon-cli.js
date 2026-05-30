#!/usr/bin/env node
// Tiny CLI shim around ensureDaemon — lets the shell wrappers (install.sh
// hermes-aify) bring up an agent's per-agent `hermes gateway run` daemon
// without re-implementing the probe/spawn/poll loop in bash/PowerShell.
//
// Usage:  node hermes-daemon-cli.js <agentId>
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

import { ensureDaemon } from "./hermes-daemon.js";

async function main() {
  const agentId = String(process.argv[2] || "").trim();
  if (!agentId) {
    process.stderr.write(
      "[hermes-daemon-cli] FATAL: missing <agentId> argument.\n" +
        "  usage: node hermes-daemon-cli.js <agentId>\n",
    );
    process.exit(2);
  }

  const result = await ensureDaemon({ agentId });
  const endpoint = result.endpoint || {};
  // One JSON line to stdout (the wrapper parses this). Key omitted on purpose.
  process.stdout.write(
    JSON.stringify({
      agentId,
      host: endpoint.host,
      port: endpoint.port,
      baseUrl: endpoint.baseUrl,
      started: !!result.started,
      version: result.version,
    }) + "\n",
  );
  process.exit(0);
}

main().catch((error) => {
  process.stderr.write(`[hermes-daemon-cli] ${error?.message || String(error)}\n`);
  process.exit(1);
});
