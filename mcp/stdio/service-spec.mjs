// What aify-comms tells a new host about itself: the registration spec served at
// `GET /.well-known/aify-service` (guided install S1, docs/superpowers/plans/guided-install/S1-spec-and-connect.md).
//
// PURE: values in, document out. The stamp-time CLI beside it reads git; the service only serves the files.
//
// ONE SOURCE FOR THE ENTRY. `register-service-cli.mjs` renders its servers with `renderStdioMcp()`, and
// writes its fixed fields from the same owner's names; tests/service-spec.test.js holds the spec's
// `fixed` and `mcp` equal to the entry that CLI writes. The env-var names come from their one owner,
// `aify-service-endpoint.mjs`. That module resolves a credential when it is imported, so the CLI seals
// the registry carrier before it imports this file (service-spec-cli.mjs).
//
// NO VALUE, ONLY NAMES. The spec carries env-var NAMES, paths and hashes: never a key, never an AIFY_*
// value (format freeze, rule 4).
//
// WHAT THE FILE-SET MANIFEST BINDS. Every git-tracked file under mcp/stdio at the commit, hashed from
// git's objects. `copy_bridge_to_native_dir` (install.sh) mirrors the whole directory, so every listed
// file is on every host; a host also holds files the manifest does not list (node_modules, untracked
// files), which a connect REPORTS rather than fails. node_modules is bound only through
// `package-lock.json`, which is a listed file: the manifest pins the lock, not the installed tree.

import { createHash } from "node:crypto";

import { API_KEY_ENV_NAMES, ENDPOINT_ENV_NAMES } from "./aify-service-endpoint.mjs";
import { SERVICE_NAME } from "./service-name.mjs";

const SPEC_VERSION = 1;
const ARTIFACT_ID = "comms-bridge";
const AGENT_STATE_PATH = "/api/v1/agent-state";

//: The two stdio servers a host runs from the bridge copy. The channel's name is derived, as the launcher
//: templates derive it (`@@SERVICE_NAME@@-channel`).
const COMMS_MCP_SERVERS = Object.freeze([
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
 * The file-set manifest: one `sha256sum` line per file (`<64 hex>  <relative/path>`, LF), sorted by
 * path, so `sha256sum -c` can check a materialised copy. Paths are relative to `mcp/stdio`.
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
 * The document. `source` is `{git: {repo, revision}}` with `manifest` (the text served beside the spec),
 * or `{pending: reason}` with no hash and no manifest when the build could not bind one. The artifact's
 * `sha256` is the hash of exactly the manifest bytes the service serves.
 */
export function commsServiceSpec({ version, source, manifest }) {
  const artifact = { id: ARTIFACT_ID, kind: "file-set", source };
  if (!source.pending) {
    artifact.sha256 = sha256(Buffer.from(manifest, "utf8"));
    artifact.manifest = { path: `/.well-known/aify-service/artifacts/${ARTIFACT_ID}.manifest` };
  }
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
