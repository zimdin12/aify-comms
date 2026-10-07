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
import fs from "node:fs";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { defaultMachineId } from "./machine-id.mjs";

const EVENTS = new Set(["turn-start", "turn-end", "blocked", "unblocked"]);

const TIMEOUT_MS = 2000;
// This process's start on the WALL clock, in microseconds: the fallback when the hook command passed no
// shell time. The same clock as $EPOCHREALTIME and the bridge's stamps (turn-event-stamp.mjs says why).
const STARTED_AT_US = Date.now() * 1000;

/**
 * When the hook fired, in host microseconds. The hooks run in the background, so two events can reach
 * the service out of order, and the service applies them by this time and host (service/api_core/
 * hook_event_order.py). The hook command passes the shell's `$EPOCHREALTIME` as AIFY_HOOK_FIRED_AT,
 * seconds with six decimals behind a `.` or a locale `,`, taken before `node` starts. It is parsed as
 * text so no microsecond is lost to a float.
 */
export function hookFiredAtUs(env = process.env, fallback = STARTED_AT_US) {
  // Up to NINE decimals: under dash the hook command falls back to GNU `date +%s.%N`, nanoseconds.
  const match = String(env.AIFY_HOOK_FIRED_AT || "").trim().match(/^(\d+)(?:[.,](\d{1,9}))?$/);
  if (!match) return fallback;
  const us = Number(match[1]) * 1_000_000 + Number((match[2] || "").slice(0, 6).padEnd(6, "0"));
  return us > 0 && Number.isSafeInteger(us) ? us : fallback;
}

/** Descriptor-only routing, reread for every event. No service resolver or credential enters this post. */
export async function postEnvAgentState(kind, { env = process.env, firedAtUs = hookFiredAtUs(env) } = {}) {
  const instance = env.AIFY_ENV_INSTANCE;
  const lifetime = env.AIFY_LIFETIME;
  const agentId = String(env.AIFY_AGENT_ID || "").trim();
  if (!EVENTS.has(kind) || !agentId || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(instance || "")
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(lifetime || "")
      || !Number.isSafeInteger(firedAtUs) || firedAtUs <= 0) return false;
  try {
    const home = process.platform === "win32" ? env.USERPROFILE : env.HOME;
    const descriptor = JSON.parse(fs.readFileSync(path.join(home || os.homedir(), ".aify", "env", `${instance}.json`), "utf8"));
    if (!descriptor || typeof descriptor !== "object" || Array.isArray(descriptor)
        || descriptor.instance !== instance || !Number.isSafeInteger(descriptor.pid) || descriptor.pid <= 0
        || typeof descriptor.startedAt !== "string" || Number.isNaN(Date.parse(descriptor.startedAt))
        || typeof descriptor.url !== "string") return false;
    // Match the descriptor's strict endpoint form before URL normalisation can repair malformed input.
    if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(descriptor.url)) return false;
    const endpoint = new URL(descriptor.url);
    const response = await fetch(new URL(`/agents/${encodeURIComponent(agentId)}/turn-event`, endpoint), {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ instance, lifetime, kind, firedAtUs }),
      redirect: "manual", credentials: "omit", signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    await response.body?.cancel();
    return response.ok;
  } catch { return false; }
}

/** Post independently to both owners. Existing readers still use the service's answer. */
export async function postAgentState(event) {
  const firedAtUs = hookFiredAtUs();
  const [service] = await Promise.all([
    postServiceAgentState(event, firedAtUs), postEnvAgentState(event, { firedAtUs }),
  ]);
  return service;
}

/** The old service post, unchanged apart from taking the shared event timestamp. */
async function postServiceAgentState(event, firedAtUs) {
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
