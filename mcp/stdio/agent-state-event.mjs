#!/usr/bin/env node
// agent-state-event.mjs <turn-start|turn-end|blocked|unblocked>
//
// What a runtime hook runs to tell the service a resident's turn started, ended, or is waiting for an
// approval. The hooks used to curl the routes directly with no `X-API-Key`; once the service enforced
// a key every one of those was answered 401 and swallowed by `|| true`, so no hook event landed.
//
// The endpoint and key come ONLY from `aify-service-endpoint.mjs`, the resolver every bridge process
// uses (environment first, then the credential the registry names for that endpoint). Hooks carry
// `AIFY_COMMS_URL`, which that module does not read, so it is copied into `AIFY_SERVER_URL` before the
// module is imported -- the module resolves at import time. A name the module already reads wins.
//
// It runs inside hooks: never prints (codex parses a hook's stdout), always exits 0, gives up after
// ~2s, and drains stdin without waiting for it to close.

import path from "node:path";
import { fileURLToPath } from "node:url";

const ROUTES = {
  "turn-start": { endpoint: "turn-start", body: null },
  "turn-end": { endpoint: "turn-end", body: null },
  blocked: { endpoint: "status-event", body: { kind: "blocked" } },
  unblocked: { endpoint: "status-event", body: { kind: "unblocked" } },
};

const TIMEOUT_MS = 2000;

/** Post one state event for `AIFY_AGENT_ID`. Resolves true when the service accepted it; never throws. */
export async function postAgentState(event) {
  const route = ROUTES[event];
  const agentId = String(process.env.AIFY_AGENT_ID || "").trim();
  if (!route || !agentId) return false;
  if (!process.env.AIFY_SERVER_URL && process.env.AIFY_COMMS_URL) {
    process.env.AIFY_SERVER_URL = process.env.AIFY_COMMS_URL;
  }
  try {
    const { httpCall, IS_REMOTE } = await import("./aify-service-endpoint.mjs");
    if (!IS_REMOTE) return false;
    // A bodyless turn-start / turn-end is the authoritative harness signal; the service treats one
    // carrying a bridgeId as a detector's and can refuse it.
    await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/${route.endpoint}`, route.body, { timeoutMs: TIMEOUT_MS });
    return true;
  } catch {
    return false;
  }
}

const isEntrypoint = (() => {
  try {
    return Boolean(process.argv[1]) && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
  } catch {
    return false;
  }
})();

if (isEntrypoint) {
  process.stdin.on("data", () => {});
  process.stdin.on("error", () => {});
  setTimeout(() => process.exit(0), TIMEOUT_MS + 500).unref();
  postAgentState(process.argv[2]).finally(() => process.exit(0));
}
