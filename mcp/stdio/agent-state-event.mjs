#!/usr/bin/env node
// agent-state-event.mjs <turn-start|turn-end|blocked|unblocked>
//
// What a runtime hook runs to tell the service a resident's turn started, ended, or is waiting for an
// approval. The hooks used to curl the routes directly with no key header; once the service enforced
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

const EVENTS = new Set(["turn-start", "turn-end", "blocked", "unblocked"]);

const TIMEOUT_MS = 2000;
const STARTED_AT = Date.now();

/**
 * When the hook fired, in host milliseconds. The hooks run in the background, so two events can reach
 * the service out of order, and the service applies them by this time (service/api_core/
 * hook_event_order.py). The hook command passes the shell's `$EPOCHREALTIME` as AIFY_HOOK_FIRED_AT
 * (seconds, with a `.` or a locale `,`), taken before `node` starts; without it, this process's start.
 */
export function hookFiredAt(env = process.env, fallback = STARTED_AT) {
  const raw = String(env.AIFY_HOOK_FIRED_AT || "").trim().replace(",", ".");
  const seconds = Number(raw);
  return raw && Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : fallback;
}

/** Post one state event for `AIFY_AGENT_ID`. Resolves true when the service accepted it; never throws. */
export async function postAgentState(event) {
  const agentId = String(process.env.AIFY_AGENT_ID || "").trim();
  if (!EVENTS.has(event) || !agentId) return false;
  if (!process.env.AIFY_SERVER_URL && process.env.AIFY_COMMS_URL) {
    process.env.AIFY_SERVER_URL = process.env.AIFY_COMMS_URL;
  }
  try {
    const { httpCall, IS_REMOTE } = await import("./aify-service-endpoint.mjs");
    if (!IS_REMOTE) return false;
    const id = encodeURIComponent(agentId);
    const opts = { timeoutMs: TIMEOUT_MS };
    const at = hookFiredAt();
    // No bridgeId: that is what keeps a turn-start / turn-end the authoritative harness signal, since
    // the service treats one carrying a bridgeId as a detector's and can refuse it. Each call is
    // spelled out so the bridge write-body gate can read its path and body.
    if (event === "turn-start") await httpCall("POST", `/agents/${id}/turn-start`, { at }, opts);
    else if (event === "turn-end") await httpCall("POST", `/agents/${id}/turn-end`, { at }, opts);
    else await httpCall("POST", `/agents/${id}/status-event`, { kind: event, at }, opts);
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
