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

import { SERVICE_NAME, upsertService } from "./service-registry.mjs";
// The fixed fields and the two servers come from the spec this service serves, so the entry written
// here and the one a connect renders from `/.well-known/aify-service` are one definition.
import { fixedFields, renderStdioMcp } from "./service-spec.mjs";

const EXIT_CONFIG = 78;
// SERVICE_NAME is imported from service-registry.mjs, which owns the registry concept.

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
  // endpointEnv and keyEnv are the names the bridge reads, declared because a runtime's per-server MCP
  // env block is key-scoped: a name the registry does not declare is INHERITED from whatever launched
  // the runtime, one service's credential reaching another's bridge. agentState is where aify-env posts.
  ...fixedFields(),
  // WHERE THE KEY FILE IS, when the installer stored one. `keyEnv` only reaches a process somebody
  // exported a variable into, and nothing on this host does that for aify-env -- which is why
  // enabling API_KEY made every advertisement 401 in silence. This is the other half: one basename
  // that aify-env resolves under its own root. Absent or blank means no new reference was supplied,
  // including an unavailable carrier on reinstall. Pass undefined to preserve the published ref;
  // intentional clearing remains the writer API's explicit credentialRef: null, not an env blank.
  credentialRef: String(process.env.CREDENTIAL_REF || "").trim() || undefined,
  mcp: renderStdioMcp(bridgeDir),
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
