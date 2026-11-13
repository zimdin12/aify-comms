#!/usr/bin/env node
// node session-for-agent.mjs <service-url> <runtime> <agent-id>
// The wrapper accepts only one controlled line; no response body or credential is printed.
import { pathToFileURL } from "node:url";
import { destinationKeyResolver } from "./aify-service-endpoint.mjs";
import { loadSettingsEnv } from "./load-env.js";
import { validateName } from "./safe-name.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function pinReply(body, runtime) {
  const agent = body?.agent ?? body;
  if (!agent || typeof agent !== "object" || Array.isArray(agent) || typeof agent.runtime !== "string" || !agent.runtime)
    return "unavailable";
  if (agent.runtime !== runtime) return "wrong-runtime";
  const handle = agent.sessionHandle ?? agent.session_handle;
  if (handle == null || handle === "") return "none";
  return typeof handle === "string" && UUID.test(handle) ? `found\t${handle}` : "unavailable";
}

export async function sessionForAgent({ url, runtime, agentId, fetchImpl = fetch, keyFor } = {}) {
  try {
    if (typeof url !== "string" || !url || typeof runtime !== "string" || !runtime) return "unavailable";
    validateName(agentId, "agent ID");
    const base = url.replace(/\/+$/, "");
    const key = (keyFor ?? destinationKeyResolver(base, { onDiagnostic: () => {} }))(base);
    const response = await fetchImpl(`${base}/api/v1/agents/${encodeURIComponent(agentId)}`, {
      headers: key ? { "X-API-Key": key } : {},
      redirect: "manual",
      signal: AbortSignal.timeout(2000),
    });
    if (response.status === 404) return "none";
    if (!response.ok) return "unavailable";
    return pinReply(await response.json(), runtime);
  } catch { return "unavailable"; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let reply = "unavailable";
  try {
    loadSettingsEnv();
    const [url, runtime, agentId] = process.argv.slice(2);
    reply = await sessionForAgent({ url, runtime, agentId });
  } catch { /* A lookup failure starts fresh, never fails the launch. */ }
  process.stdout.write(`${reply}\n`);
}
