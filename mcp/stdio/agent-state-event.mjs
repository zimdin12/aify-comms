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
import { defaultMachineId } from "./machine-id.mjs";

const EVENTS = new Set(["turn-start", "turn-end", "blocked", "unblocked"]);

const TIMEOUT_MS = 2000;
// This process's start, in microseconds: the fallback when the hook command passed no shell time.
const STARTED_AT_US = Math.round((performance.timeOrigin + performance.now()) * 1000);

/**
 * When the hook fired, in host microseconds. The hooks run in the background, so two events can reach
 * the service out of order, and the service applies them by this time and host (service/api_core/
 * hook_event_order.py). The hook command passes the shell's `$EPOCHREALTIME` as AIFY_HOOK_FIRED_AT,
 * seconds with six decimals behind a `.` or a locale `,`, taken before `node` starts. It is parsed as
 * text so no microsecond is lost to a float.
 */
export function hookFiredAtUs(env = process.env, fallback = STARTED_AT_US) {
  const match = String(env.AIFY_HOOK_FIRED_AT || "").trim().match(/^(\d+)(?:[.,](\d{1,6}))?$/);
  if (!match) return fallback;
  const us = Number(match[1]) * 1_000_000 + Number((match[2] || "").padEnd(6, "0"));
  return us > 0 && Number.isSafeInteger(us) ? us : fallback;
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
    const firedAtUs = hookFiredAtUs();
    const machineId = defaultMachineId();
    // No bridgeId: that is what keeps a turn-start / turn-end the authoritative harness signal, since
    // the service treats one carrying a bridgeId as a detector's and can refuse it. Each call is
    // spelled out so the bridge write-body gate can read its path and body.
    if (event === "turn-start") await httpCall("POST", `/agents/${id}/turn-start`, { firedAtUs, machineId }, opts);
    else if (event === "turn-end") await httpCall("POST", `/agents/${id}/turn-end`, { firedAtUs, machineId }, opts);
    else await httpCall("POST", `/agents/${id}/status-event`, { kind: event, firedAtUs, machineId }, opts);
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
