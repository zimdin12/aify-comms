#!/usr/bin/env node

import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import { defaultMachineId } from "./runtimes.js";
import { writeRuntimeMarker, removeRuntimeMarker } from "./runtime-markers.js";
import { claudeAifyReceiptLine } from "./aify-console-markers.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";

loadSettingsEnv();

// Windows + Docker Desktop: `localhost` resolves to IPv6 ::1 first, but
// Docker Desktop's IPv6 port forwarding is unreliable — HTTP requests
// time out silently and the channel bridge cannot claim dispatches.
// Force the IPv4 loopback. Benign on Linux/macOS (same loopback address).
function coerceLoopbackToIPv4(url) {
  return String(url || "").replace(
    /^(https?:\/\/)localhost(?=[:\/]|$)/i,
    "$1127.0.0.1",
  );
}

const SERVER_URL = coerceLoopbackToIPv4(
  process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "",
);
function splitServerUrls(value) {
  return String(value || "")
    .split(/[,\s]+/)
    .map(item => coerceLoopbackToIPv4(item.trim().replace(/\/+$/, "")))
    .filter(Boolean);
}
function uniqueServerUrls(urls) {
  const seen = new Set();
  const result = [];
  for (const url of urls) {
    if (!url || seen.has(url)) continue;
    seen.add(url);
    result.push(url);
  }
  return result;
}
function defaultFallbackServerUrls(primary) {
  if (!/^https?:\/\/(localhost|127\.0\.0\.1)(?::|\/|$)/i.test(String(primary || ""))) return [];
  return ["http://host.docker.internal:8800", "http://192.168.100.10:8800"];
}
const SERVER_URLS = uniqueServerUrls([
  SERVER_URL,
  ...splitServerUrls(process.env.CLAUDE_MCP_FALLBACK_URLS || process.env.AIFY_SERVER_FALLBACK_URLS || ""),
  ...defaultFallbackServerUrls(SERVER_URL),
]);
let ACTIVE_SERVER_URL = SERVER_URLS[0] || "";
const API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";
const MACHINE_ID = defaultMachineId();
// Per-agent channel-sidecar bridge id. MUST be agent-scoped: bridge_instances.id
// is the PRIMARY KEY, so a machine-global `channel-<machine>` id let only ONE
// co-located claude agent own the row — every other agent's sidecar could not
// insert/refresh its liveness heartbeat (lost heartbeats, wrong status,
// agent_id thrash, and cross-agent supersession that permanently blocked
// claims). Scoping by agentId gives each agent its own row. The bound agentId
// is only known per-poll (readBoundAgentId), so the id is computed per-call.
// (operator-reported 2026-05-31; holistic-review F1)
const CHANNEL_BRIDGE_PREFIX = `channel-${MACHINE_ID}`;
export function channelBridgeId(agentId) {
  const id = String(agentId || "").trim();
  return id ? `${CHANNEL_BRIDGE_PREFIX}-${id}` : CHANNEL_BRIDGE_PREFIX;
}
const POLL_MS = Number(process.env.AIFY_COMMS_CHANNEL_POLL_MS || process.env.AIFY_CLAUDE_CHANNEL_POLL_MS || 3000);
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));
const IS_MAIN = Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

// Write our claude-code runtime marker from this long-lived bridge process.
// This must happen here, not in the wrapper's bash CLI call, because on
// Git Bash for Windows `$$` is an MSYS shell PID and isProcessAlive() from
// node cannot see it — listRuntimeMarkers would auto-delete the wrapper's
// marker on first read. node's process.pid is a real Windows PID.
const MARKER_CWD = process.cwd();
if (IS_MAIN) {
  try {
    writeRuntimeMarker("claude-code", MARKER_CWD, {
      channelEnabled: true,
      parentPid: process.ppid || "",
    });
  } catch (error) {
    console.error("[aify-channel] failed to write runtime marker:", error?.message || String(error));
  }
}

function removeOwnMarker() {
  try {
    removeRuntimeMarker("claude-code", MARKER_CWD);
  } catch {
    // best effort — a dead PID will get auto-cleaned on next listRuntimeMarkers anyway
  }
}
if (IS_MAIN) {
  process.on("exit", removeOwnMarker);
  process.on("SIGINT", () => { removeOwnMarker(); process.exit(130); });
  process.on("SIGTERM", () => { removeOwnMarker(); process.exit(143); });
}

// No activeRunId tracking. The channel bridge claims a dispatch, delivers
// it to the Claude session via MCP notification, and marks the run delivered
// in the same tick. It cannot track whether Claude actually processed the
// work, so it must not keep channel deliveries in "running" state.

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readBoundAgentId() {
  // Read the agent binding from the PID-keyed temp file written by
  // server.js on comms_register. The file is keyed by ppid because both
  // server.js and this process are children of the same Claude Code
  // process — they share the same ppid.
  const candidates = [
    path.join(TMP_DIR, `aify-agent-${process.ppid || process.pid}`),
  ];
  for (const candidate of candidates) {
    try {
      const binding = readAgentBindingFile({ pid: process.ppid || process.pid, dir: TMP_DIR });
      if (binding.agentId) return binding.agentId;
    } catch {
      // keep looking
    }
  }
  return "";
}

async function httpCall(method, endpoint, body = null) {
  if (!SERVER_URL) return null;
  const options = { method, headers: {} };
  if (API_KEY) options.headers["X-API-Key"] = API_KEY;
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let lastError;
  for (const baseUrl of uniqueServerUrls([ACTIVE_SERVER_URL, ...SERVER_URLS])) {
    const url = `${baseUrl}/api/v1${endpoint}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      if (!res.ok) {
        const text = await res.text();
        const error = new Error(`HTTP ${res.status}: ${text}`);
        error.status = res.status;
        error.serverUrl = baseUrl;
        throw error;
      }
      ACTIVE_SERVER_URL = baseUrl;
      return res.json();
    } catch (error) {
      if (error?.name === "AbortError") {
        lastError = new Error(`HTTP ${method} ${endpoint} timed out after ${HTTP_TIMEOUT_MS}ms`);
        lastError.name = "TimeoutError";
        lastError.serverUrl = baseUrl;
      } else {
        error.serverUrl = error.serverUrl || baseUrl;
        lastError = error;
      }
      const code = String(error?.code || "");
      const message = String(error?.message || "");
      const transient = /ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|EPIPE|socket hang up|fetch failed|network/i.test(code + " " + message);
      if (!transient) throw lastError;
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError || new Error(`HTTP ${method} ${endpoint} failed`);
}

export function dispatchContent(agentId, run) {
  const body = String(run.body || "").replace(/```/g, "'''");
  const priority = (run.priority || "normal").toLowerCase();
  const priorityLabel =
    priority === "urgent" ? "URGENT" :
    priority === "high" ? "HIGH" :
    "NORMAL";
  const actionLine =
    priority === "urgent" ? "Drop current work and handle this immediately." :
    priority === "high" ? "Read before continuing current work." :
    "Handle when you reach a natural break.";
  return [
    claudeAifyReceiptLine(),
    `[${priorityLabel}] ${run.from || "unknown"} → ${agentId}: ${run.subject || "(no subject)"}`,
    actionLine,
    `From: ${run.from}`,
    `Subject: ${run.subject}`,
    priority !== "normal" ? `Priority: ${priority.toUpperCase()}` : "",
    run.messageId ? `Message ID: ${run.messageId}` : "",
    "",
    "Handle this directly in the current session.",
    run.messageId
      ? `When you reply, include inReplyTo="${run.messageId}" so the sender sees your response linked to their original message.`
      : "Reply through aify when the task is done.",
    "",
    "```",
    body,
    "```",
  ].filter(Boolean).join("\n");
}

function controlContent(agentId, control) {
  const body = String(control.body || "").replace(/```/g, "'''");
  const lines = [
    `Aify ${control.action} for agent "${agentId}".`,
    control.from ? `Requested by: ${control.from}` : "",
  ];
  if (body) {
    lines.push("", "```", body, "```");
  }
  if (control.action === "interrupt") {
    lines.push("", "Stop your current task as soon as practical. Send a brief status reply.");
  } else if (control.action === "steer") {
    lines.push("", "Apply this guidance to your current work.");
  }
  return lines.filter(Boolean).join("\n");
}

const mcp = new Server(
  { name: "aify-comms-channel", version: "4.0.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
    },
    instructions:
      'Events from aify resident dispatch arrive as <channel source="aify-comms-channel" ...>. ' +
      "These are real wake-up events for the current session. Handle them directly in this session. " +
      "Use the existing comms_* tools to coordinate and reply. " +
      "When a dispatch event includes Message ID, include that same value as inReplyTo when you reply so the run can close automatically.",
  },
);

// Pure decision: given a /agents/{id} snapshot, should the channel
// bridge re-pulse turn_busy on this poll cycle? Exported for tests.
// Returns { repulse: boolean, runId: string }. See the call site +
// 2026-05-23 feedback-loop discussion in pollLoop for rationale.
export function decideRepulse(agentSnapshot = {}) {
  const dispatchState = agentSnapshot.dispatchState || {};
  const hasActiveRun = Boolean(dispatchState.hasActiveRun);
  if (!hasActiveRun) return { repulse: false, runId: "" };
  const activeRunId = String(dispatchState.activeRun?.runId || "");
  return { repulse: true, runId: activeRunId };
}

async function emitChannel(content, meta = {}) {
  await mcp.notification({
    method: "notifications/claude/channel",
    params: {
      content,
      meta,
    },
  });
}

function isChannelRun(run) {
  return String(run?.executionMode || "").trim().toLowerCase() === "channel";
}

async function reportTurnBusy(agentId, { busy, runId = "" } = {}) {
  await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/heartbeat`, {
    bridgeId: channelBridgeId(agentId),
    turnBusy: !!busy,
    turnRunId: runId,
    turnRuntime: "claude-code",
  });
}

async function markDispatchDelivered(run) {
  // Any dispatch with require_reply=true stays in 'delivered' status until
  // the agent's explicit reply (via comms_send with inReplyTo) closes it.
  // This applies symmetrically to channel-route and resident-execution_mode
  // dispatches — both pass through this delivery path. Without it, resident
  // require_reply runs auto-completed on delivery and the dashboard had no
  // signal that the agent still owed a reply. Server-side derivation
  // (_current_channel_awaiting_reply_run_row) lights up "working" while
  // any such run is 'delivered'.
  const channelRun = isChannelRun(run);
  const requireReply = !!run?.requireReply;
  const runId = String(run?.id || "");
  const awaitingReply = requireReply;
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: awaitingReply ? "delivered" : "completed",
    summary: channelRun
      ? "Delivered to Claude channel session; awaiting explicit reply"
      : "Delivered to Claude resident session; awaiting explicit reply",
    runtime: "claude-code",
    agentStatus: "active",
    appendEvent: channelRun
      ? "Delivered to Claude channel bridge"
      : "Delivered and completed by channel bridge",
    eventType: "delivered",
  });
}

async function markDispatchDeliveryFailed(runId, error) {
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "failed",
    error: error?.message || String(error),
    runtime: "claude-code",
    agentStatus: "active",
    appendEvent: `Claude channel delivery failed: ${error?.message || String(error)}`,
    eventType: "failed",
  });
}

// Per-agent "I delivered work recently — assistant is probably still
// running" timestamp. While set and within TURN_REFRESH_MAX_AGE_MS, we
// refresh turn_busy=true on every poll cycle. The Stop hook is the
// authoritative clear signal (POST /agents/{id}/turn-end fires when
// the assistant turn ends); this map's 10-min ceiling is the safety
// upper bound for a single turn so a missed Stop hook doesn't pin
// "working" forever.
//
// Previous design polled the dispatch_run's status and dropped
// tracking when it went non-'delivered'. Wrong for require_reply=0
// dispatches: the server completes delivery-only runs IMMEDIATELY
// on delivery, so the next poll cycle (~3s later) saw status =
// 'completed' and dropped tracking → 120s later turn_busy staled →
// status flipped to "online" while the assistant was still composing
// a long multi-tool-call reply. Operator-reported 2026-05-22:
// "you are showing online. check now" + "i wrote you in here (in
// console) so i would not affect that status. but it should affect
// it, because you are running in claude-aify i think" — confirming
// the bridge IS claude-aify-wrapped but the refresh wasn't keeping up.
//
// New: timestamp-based tracker. Set on EVERY claim. Re-pulse on every
// poll cycle while within the window. Drop only on Stop-hook clear
// (we read server's turn_busy state) or window expiry.
const LAST_DELIVERED_AT_PER_AGENT = new Map();
const TURN_REFRESH_MAX_AGE_MS = 10 * 60 * 1000;

async function pollLoop() {
  const stopLiveness = startLivenessHeartbeat({
    intervalMs: 30_000,
    beat: async () => {
      if (!SERVER_URL) return;
      const id = readBoundAgentId();
      if (!id) return;
      await httpCall("POST", `/agents/${encodeURIComponent(id)}/heartbeat`, {
        bridgeId: channelBridgeId(id),
        bridgeKind: "channel-sidecar",
        liveness: true,
      });
    },
  });
  try {
  while (true) {
    try {
      if (!SERVER_URL) {
        await sleep(POLL_MS);
        continue;
      }

      const agentId = readBoundAgentId();
      if (!agentId) {
        await sleep(POLL_MS);
        continue;
      }

      // Drain all queued dispatches, bundle into one notification.
      // One combined message is less disruptive than 20 separate interruptions.
      const batch = [];
      for (let i = 0; i < 20; i++) {
        const claim = await httpCall("POST", "/dispatch/claim", {
          agentId,
          machineId: MACHINE_ID,
          bridgeId: channelBridgeId(agentId),
          // Standalone channel sidecar (not a wrapper-PTY child). Claude's
          // claim is accepted by the service by runtime regardless, so this is
          // declarative/symmetric with hermes-channel.js — see the service
          // gate _bridge_claim_block_reason.
          bridgeKind: "channel-sidecar",
          executionModes: ["channel", "resident"],
        });
        // Mode FSM release signal (Task 4.1): the operator switched this agent
        // to resident, so this managed sidecar is no longer the driver. Stop
        // driving and exit the poll loop gracefully — the resident TUI/CLI now
        // owns the session (one-driver invariant).
        if (claim?.release) {
          console.error(
            `[claude-channel] released: agent '${agentId}' switched to resident; sidecar stopping.`,
          );
          return;
        }
        const executionMode = String(claim?.run?.executionMode || "").trim().toLowerCase();
        if (!claim?.run || !["channel", "resident"].includes(executionMode)) break;
        batch.push(claim.run);
      }
      // Periodic turn_busy refresh ONLY for dispatches this bridge
      // claimed (tracked locally via LAST_DELIVERED_AT_PER_AGENT).
      //
      // Previous design also did a slow-tick GET /agents/{id} to catch
      // UserPromptSubmit-initiated turns and refresh based on server
      // status — that created a feedback loop (operator-reported
      // 2026-05-22 "sc-manager stuck working with no active run"):
      //   1. UserPromptSubmit (or any path) sets turn_busy=1 once
      //   2. Server reports status='working' (because turn_busy is fresh)
      //   3. Bridge's slow-tick GET reads 'working' → re-pulses turn_busy=1
      //   4. turn_updated_at never expires → step 2 keeps holding → infinite loop
      //
      // UserPromptSubmit-initiated turns now rely on the 120s
      // server-side TURN_BUSY_STALE_SECONDS window + the
      // authoritative Stop hook clear. Brief flicker for >120s
      // assistant turns is acceptable; stuck-working-forever is not.
      if (batch.length === 0) {
        const trackedAt = LAST_DELIVERED_AT_PER_AGENT.get(agentId);
        if (trackedAt && Date.now() - trackedAt > TURN_REFRESH_MAX_AGE_MS) {
          LAST_DELIVERED_AT_PER_AGENT.delete(agentId);
        }
        if (LAST_DELIVERED_AT_PER_AGENT.has(agentId)) {
          try {
            const agentRes = await httpCall("GET", `/agents/${encodeURIComponent(agentId)}`);
            const decision = decideRepulse(agentRes?.agent || {});
            // Re-pulse condition: ONLY if there's an actual unsettled
            // dispatch run (hasActiveRun = require_reply=true awaiting
            // reply, or status=running). NOT based on serverStatus alone,
            // which is DERIVED from turn_busy=1 — using it as a re-pulse
            // trigger creates the self-reinforcing feedback loop
            // (operator-reported 2026-05-23 "your and sc-coder status
            // were stuck at working"):
            //   1. dispatch delivered → turn_busy=1, LAST_DELIVERED set
            //   2. dispatch marked completed (require_reply=false case)
            //     → hasActiveRun=false but turn_busy still 1 fresh
            //   3. server derives status='working' from turn_busy=1
            //   4. bridge GET → reads 'working' → re-pulses turn_busy=1
            //   5. step 3 keeps holding → infinite loop until
            //      TURN_REFRESH_MAX_AGE_MS (10 min) clears the tracker.
            //
            // Why hasActiveRun is the right anchor: it's POSITIVE
            // evidence the agent owes work (a delivered run that hasn't
            // been replied to, or a claimed/running run still in flight).
            // For UserPromptSubmit-initiated turns (no dispatch),
            // turn_busy relies on the server-side TURN_BUSY_STALE_SECONDS
            // window + the authoritative Stop hook clear. Brief flicker
            // for >120s assistant turns is acceptable; stuck-working-
            // forever is not.
            if (decision.repulse) {
              await reportTurnBusy(agentId, { busy: true, runId: decision.runId }).catch(() => {});
            } else {
              // No unsettled run — agent is genuinely idle (Stop hook
              // fired, run completed externally, etc.). Drop tracking.
              LAST_DELIVERED_AT_PER_AGENT.delete(agentId);
            }
          } catch {
            // Transient — best-effort, do nothing this cycle.
          }
        }
      }

      if (batch.length === 1) {
        const run = batch[0];
        LAST_DELIVERED_AT_PER_AGENT.set(agentId, Date.now());
        // Pulse turn_busy=true on EVERY dispatch delivery (any execution_mode).
        // The agent is about to receive a wake-up and start processing — show
        // "working" in the dashboard until the heartbeat staleness window
        // (server-side TURN_BUSY_STALE_SECONDS, currently 120s) clears it OR
        // a new dispatch re-pulses to extend it. The previous channel-only
        // gate + immediate-clear-in-finally meant resident dispatches got no
        // pulse at all and channel dispatches got a microsecond pulse, neither
        // observable in dashboard sampling.
        await reportTurnBusy(agentId, { busy: true, runId: run.id }).catch(() => {});
        try {
          await emitChannel(dispatchContent(agentId, run), {
            event_type: "dispatch",
            agent_id: agentId,
            run_id: run.id,
            from_agent: run.from || "",
            message_id: run.messageId || "",
            priority: run.priority || "normal",
          });
          await markDispatchDelivered(run);
        } catch (error) {
          await markDispatchDeliveryFailed(run.id, error);
          throw error;
        }
        // Intentional: NO finally { busy=false }. The agent is now working
        // on the reply; clearing here loses the signal. Server stale-window
        // closes it if no further pulses arrive. require_reply runs ALSO
        // get the server-side channel-awaiting-reply derivation for tighter
        // "working" tracking until the reply lands.
      } else if (batch.length > 1) {
        LAST_DELIVERED_AT_PER_AGENT.set(agentId, Date.now());
        await reportTurnBusy(agentId, { busy: true, runId: batch[0].id }).catch(() => {});
        const combined = batch.map((run, i) => `--- Message ${i + 1} of ${batch.length} ---\n${dispatchContent(agentId, run)}`).join("\n\n");
        const highestPriority = batch.some(r => r.priority === "urgent") ? "urgent" : batch.some(r => r.priority === "high") ? "high" : "normal";
        try {
          await emitChannel(combined, {
            event_type: "dispatch_batch",
            agent_id: agentId,
            count: batch.length,
            priority: highestPriority,
          });
          for (const run of batch) {
            await markDispatchDelivered(run);
          }
        } catch (error) {
          for (const run of batch) {
            await markDispatchDeliveryFailed(run.id, error);
          }
          throw error;
        }
      }

      // Poll for controls (interrupt/steer) independently of run tracking.
      // This makes comms_run_interrupt and comms_run_steer work for Claude
      // the same way they work for Codex — the sender uses the same tool
      // regardless of target runtime.
      const controlClaim = await httpCall("POST", "/dispatch/controls/claim", {
        agentId,
        machineId: MACHINE_ID,
      });
      for (const control of controlClaim?.controls || []) {
        await emitChannel(controlContent(agentId, control), {
          event_type: "control",
          agent_id: agentId,
          run_id: control.runId || "",
          action: control.action || "",
          from_agent: control.from || "",
        });
        await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
          status: "completed",
          response: "Delivered to Claude resident session",
        });
      }
    } catch (error) {
      console.error("[aify-channel] tick error:", error?.message || String(error));
    }

    await sleep(POLL_MS);
  }
  } finally {
    stopLiveness();
  }
}

if (IS_MAIN) {
  await mcp.connect(new StdioServerTransport());
  pollLoop().catch((error) => {
    console.error("[aify-channel] fatal:", error);
    process.exit(1);
  });
}
