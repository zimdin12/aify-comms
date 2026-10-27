// The comms tools for one Claude launch (0.9.11 slice 4): the service's `/mcp/http`, called as this launch's agent.
//
// claude-aify writes this to a per-session `--mcp-config` file and removes it on exit, so nothing machine-wide
// holds a per-agent value. The URL path and the identity header are this service's contract, so the service's
// installed copy prints them and the launcher only places the file. The key is resolved the one way every
// comms process resolves it (`destinationKeyResolver`): an exported key, else the credential aify-env holds for
// exactly this endpoint.
//
//   node comms-http-entry.mjs <endpoint> <server name> [agent id]   → the --mcp-config JSON on stdout
//
// A launch with no agent id gets the tools without the header: anything that acts as "you" refuses, reads work.

import { pathToFileURL } from "node:url";

import { destinationKeyResolver } from "./aify-service-endpoint.mjs";

/** PURE. The `--mcp-config` document for one launch. */
export function commsHttpEntry({ serviceName, endpoint, agentId = "", apiKey = "" }) {
  const headers = {
    ...(agentId ? { "X-Aify-Agent-Id": agentId } : {}),
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
  };
  return { mcpServers: { [serviceName]: { type: "http", url: `${String(endpoint).replace(/\/+$/, "")}/mcp/http`, headers } } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [endpoint = "", serviceName = "", agentId = ""] = process.argv.slice(2);
  if (!endpoint || !serviceName) {
    console.error("usage: comms-http-entry.mjs <endpoint> <server name> [agent id]");
    process.exit(2);
  }
  const apiKey = destinationKeyResolver(endpoint)(endpoint);
  process.stdout.write(`${JSON.stringify(commsHttpEntry({ serviceName, endpoint, agentId, apiKey }))}\n`);
}
