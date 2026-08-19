#!/usr/bin/env node
// Register aify-comms in the shared service registry at install time.
//
//   register-service-cli.mjs <registry-path> <endpoint> <bridge-dir>
//
// Thin wiring over service-registry.mjs: it supplies the filesystem and the values only an install
// knows, and the deciding lives in the pure module beside it where it is tested.
//
// This is the half of the split the operator asked for by name: installing a SERVICE should register
// that service so the launchers know it exists. Installing aify-wrapper is never the goal.
//
// Exit 78 on a registry we cannot safely rewrite, so the install fails loudly rather than silently
// removing another service's entry.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

import { ENDPOINT_ENV_NAMES } from "./aify-service-endpoint.mjs";
import { upsertService } from "./service-registry.mjs";

const EXIT_CONFIG = 78;
const SERVICE_NAME = "aify-comms";

const [, , registryPath, endpoint, bridgeDir] = process.argv;

if (!registryPath || !endpoint || !bridgeDir) {
  process.stderr.write("usage: register-service-cli.mjs <registry-path> <endpoint> <bridge-dir>\n");
  process.exit(EXIT_CONFIG);
}

let existing = "";
try {
  existing = readFileSync(registryPath, "utf8");
} catch (error) {
  if (error.code !== "ENOENT") {
    process.stderr.write(`registry: cannot read ${registryPath}: ${error.message}\n`);
    process.exit(EXIT_CONFIG);
  }
  // ENOENT is the first service ever registered on this host, which is ordinary.
}

const result = upsertService(existing, SERVICE_NAME, {
  endpoint,
  // Declared from the bridge's own list rather than typed here. A name the bridge reads but the
  // registry does not declare gets INHERITED from whatever launched the runtime, because a runtime's
  // per-server MCP env block is key-scoped.
  endpointEnv: ENDPOINT_ENV_NAMES,
  mcp: [
    { name: "aify-comms", command: "node", args: [`${bridgeDir}/server.js`] },
    { name: "aify-comms-channel", command: "node", args: [`${bridgeDir}/claude-channel.js`] },
  ],
});

if (!result.ok) {
  process.stderr.write(`registry ${registryPath} was not updated:\n`);
  for (const problem of result.errors) process.stderr.write(`  - ${problem}\n`);
  process.stderr.write("  aify-comms will still work; launchers just will not learn about it here.\n");
  process.exit(EXIT_CONFIG);
}

mkdirSync(dirname(registryPath), { recursive: true });
writeFileSync(registryPath, result.text);
process.stdout.write(`registered ${SERVICE_NAME} at ${endpoint} in ${registryPath}\n`);
