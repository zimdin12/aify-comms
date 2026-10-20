// What aify-comms tells a new host about itself: the registration spec served at
// `GET /.well-known/aify-service` (guided install S1, docs/superpowers/plans/guided-install/S1-spec-and-connect.md).
//
// PURE: values in, document out. The stamp-time CLI beside it reads git; the service only serves the file.
//
// ONE SOURCE FOR THE ENTRY. `register-service-cli.mjs` writes this host's registry entry from the same
// `fixedFields()` and `COMMS_MCP_SERVERS`, so the entry a connect renders from the spec and the entry the
// installer writes today cannot drift apart.
//
// NO VALUE, ONLY NAMES. The spec carries env-var NAMES, paths and hashes: never a key, never an AIFY_*
// value (format freeze, rule 4). Nothing here reads the environment or a credential.

import { createHash } from "node:crypto";

import { API_KEY_ENV_NAMES, ENDPOINT_ENV_NAMES } from "./service-env-names.mjs";
import { SERVICE_NAME } from "./service-name.mjs";

export const SPEC_VERSION = 1;
export const ARTIFACT_ID = "comms-bridge";
export const AGENT_STATE_PATH = "/api/v1/agent-state";

//: The two stdio servers a host runs from the bridge copy. The channel's name is derived, as the launcher
//: templates derive it (`@@SERVICE_NAME@@-channel`).
export const COMMS_MCP_SERVERS = Object.freeze([
  Object.freeze({ name: SERVICE_NAME, entry: "server.js" }),
  Object.freeze({ name: `${SERVICE_NAME}-channel`, entry: "claude-channel.js" }),
]);

/** The registry fields copied verbatim: no endpoint, no mcp, no credential reference (S1 rules). */
export function fixedFields() {
  return {
    endpointEnv: [...ENDPOINT_ENV_NAMES],
    keyEnv: [...API_KEY_ENV_NAMES],
    agentState: { path: AGENT_STATE_PATH },
  };
}

/** Today's stdio rendering, as `register-service-cli.mjs` writes it for a bridge copy at `installRoot`. */
export function renderStdioMcp(installRoot) {
  return COMMS_MCP_SERVERS.map(({ name, entry }) => ({ name, command: "node", args: [`${installRoot}/${entry}`] }));
}

/**
 * The file-set manifest: one `sha256sum` line per file, sorted by path, so `sha256sum -c` can check a
 * materialised copy. Paths are relative to `mcp/stdio`.
 *
 * @param {{path: string, bytes: Buffer}[]} files
 */
export function manifestText(files) {
  const lines = files.map(({ path, bytes }) => {
    if (!path || /[\r\n\\]/.test(path) || path.startsWith("/") || path.split("/").includes("..")) {
      throw new Error(`manifest: unusable path ${JSON.stringify(path)}`);
    }
    return { path, line: `${sha256(bytes)}  ${path}\n` };
  });
  lines.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  return lines.map(({ line }) => line).join("");
}

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/**
 * The document. `source` is `{git: {repo, revision}}` with the manifest's hash, or `{pending: reason}`
 * with no hash at all when the build could not bind one.
 */
export function commsServiceSpec({ version, source, manifestSha256 }) {
  const artifact = { id: ARTIFACT_ID, kind: "file-set", source };
  if (!source.pending) artifact.sha256 = manifestSha256;
  artifact.entries = COMMS_MCP_SERVERS.map(({ entry }) => entry);
  return {
    specVersion: SPEC_VERSION,
    name: SERVICE_NAME,
    version,
    fixed: fixedFields(),
    artifacts: [artifact],
    mcp: COMMS_MCP_SERVERS.map(({ name, entry }) => ({ name, artifact: ARTIFACT_ID, entry })),
    secrets: [{ field: "credentialRef", required: true, probe: "api" }],
    verify: {
      health: { path: "/health" },
      probes: { api: { path: "/api/v1/agents", header: "x-api-key" } },
    },
  };
}
