#!/usr/bin/env node
// Which aify agent owns a runtime's session handle? Printed on stdout, or nothing.
//
//   node agent-for-handle.mjs <service-url> <runtime> <handle>
//
// The launchers (`claude-aify --resume <id>`, `codex-aify --resume <thread>`, `hermes-aify --resume`)
// ask this when no `--aify-agent` was given. They used to `curl /api/v1/agents` themselves with no
// `X-API-Key`, so the moment a service required a key the lookup got a 401 and recovery silently fell
// back to the local store (v0.7 docs review, D36). A launcher holds no key; the bridge knows where one
// is, so the lookup lives beside the bridge and resolves the key the way every bridge call does.
//
// IT CAN NEVER FAIL A LAUNCH: every failure prints nothing and exits 0, and the launcher's own
// fallbacks take over.

import { pathToFileURL } from "node:url";

import { destinationKeyResolver } from "./aify-service-endpoint.mjs";

const TIMEOUT_MS = 2000;

export async function agentForHandle({ url, runtime, handle, fetchImpl = fetch, keyFor = destinationKeyResolver(url) }) {
  if (!url || !runtime || !handle) return "";
  const base = String(url).replace(/\/+$/, "");
  const key = keyFor(base);
  // NEVER FOLLOWED: `fetch` re-sends custom headers on a redirect, so a 302 would hand the key to
  // whatever origin it names (v0.7 review reproduced exactly that). A redirect answers nothing here.
  const response = await fetchImpl(`${base}/api/v1/agents`, {
    headers: key ? { "X-API-Key": key } : {},
    redirect: "manual",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!response.ok) return "";
  const agents = (await response.json())?.agents || {};
  for (const [id, agent] of Object.entries(agents)) {
    const bound = String(agent?.sessionHandle || agent?.session_handle || "");
    if (bound && bound === handle && String(agent?.runtime || "") === runtime) return id;
  }
  return "";
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [url, runtime, handle] = process.argv.slice(2);
  try {
    const id = await agentForHandle({ url, runtime, handle });
    if (id) process.stdout.write(id);
  } catch {
    // Nothing: the launcher falls back to its local store and says so.
  }
}
