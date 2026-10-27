#!/usr/bin/env node

import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { makeDispatchReceipts } from "./channel-dispatch-receipts.mjs";
import { loadSettingsEnv } from "./load-env.js";
import { defaultMachineId } from "./machine-id.mjs";
import { writeRuntimeMarker, removeRuntimeMarker } from "./runtime-markers.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
import { BRIDGE_BUILD_TAG } from "./bridge-build.mjs";
import { AIFY_VERSION } from "./version.js";
import { boundAgentId } from "./bound-agent-id.mjs";
// The server-URL helpers have ONE owner: `aify-service-endpoint.mjs`, which imports nothing and so
// cannot cycle. This module declared its own copies of all four until v0.5.4 — byte-identical for
// three of them and the same code with a different comment for the fourth, which is what a fork
// looks like right up until one copy gets a fix and the others do not.
import {
  coerceLoopbackToIPv4,
  destinationKeyResolver,
  defaultFallbackServerUrls,
  splitServerUrls,
  uniqueServerUrls,
} from "./aify-service-endpoint.mjs";
// Content and the re-pulse decision left for `claude-channel-content.js` in v0.5.4 — pure, and
// therefore testable without a bridge. Importers were repointed at the new module rather than
// re-exported through this one: two import paths for one name is how a fork starts.
import { controlContent, dispatchContent } from "./claude-channel-content.js";
import { TRUST_RULE } from "./tool-response-format.mjs";
import { channelSource, isThisRoutesProducer } from "./channel-producer.mjs";
import { ResidentDuties, holdsResidentDuties } from "./resident-duties.mjs";
import { SHARED_FILE_TOOLS, callSharedFileTool } from "./shared-file-transfer.mjs";

// The interface aify-wrapper reads before it chooses the plugin route: runChannel() below runs the version-1 producer gate
// (channel-producer.mjs). It speaks for claude-channel-plugin.mjs too, which runs this runChannel().
export const CHANNEL_PRODUCER_GATE = 1;

loadSettingsEnv();

const SERVER_URL = coerceLoopbackToIPv4(
  process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "",
);
const SERVER_URLS = uniqueServerUrls([
  SERVER_URL,
  ...splitServerUrls(process.env.CLAUDE_MCP_FALLBACK_URLS || process.env.AIFY_SERVER_FALLBACK_URLS || ""),
  ...defaultFallbackServerUrls(SERVER_URL),
]);
let ACTIVE_SERVER_URL = SERVER_URLS[0] || "";
// Per destination, and the registry credential as well as the environment (v0.7, B5).
const keyFor = destinationKeyResolver(SERVER_URL);
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
// Dormant back-off after a mode-FSM `release` (we are not the active driver). Long enough
// not to spam claims while displaced, short enough that a healed driver_state (the server's
// adopt-live-resident self-heal) restores delivery within a minute.
const RELEASE_RECHECK_MS = Math.max(5000, Number(process.env.AIFY_CHANNEL_RELEASE_RECHECK_MS || 60_000));
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));
// Long-poll for the dispatch claim — the server holds it open up to CLAIM_WAIT_MS waiting
// for work instead of this sidecar re-polling every POLL_MS. Single-agent sidecar, so no
// serialization concern. Keep under the server cap (30s); HTTP timeout must exceed the hold.
// Set AIFY_CLAIM_WAIT_MS=0 to revert to short-poll. See mcp/stdio/server.js + service/longpoll.py.
const CLAIM_WAIT_MS = Math.max(0, Math.min(28000, Number(process.env.AIFY_CLAIM_WAIT_MS ?? 20000)));
const CLAIM_OPTS = CLAIM_WAIT_MS > 0 ? { timeoutMs: CLAIM_WAIT_MS + 8000 } : {};
const IS_MAIN = Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

const MARKER_CWD = process.cwd();

function removeOwnMarker() {
  try {
    removeRuntimeMarker("claude-code", MARKER_CWD);
  } catch {
    // best effort — a dead PID will get auto-cleaned on next listRuntimeMarkers anyway
  }
}

// No activeRunId tracking. The channel bridge claims a dispatch, delivers
// it to the Claude session via MCP notification, and marks the run delivered
// in the same tick. It cannot track whether Claude actually processed the
// work, so it must not keep channel deliveries in "running" state.

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The binding read is shared (`bound-agent-id.mjs`); the FALLBACK is this module's own choice.
// This one returns "" when no binding file exists, where both hermes bridges fall back to
// AIFY_AGENT_ID. That difference predates the dedupe and is preserved deliberately — it is now
// one visible argument instead of a difference buried in three near-identical bodies.
function readBoundAgentId() {
  return boundAgentId({ dir: TMP_DIR });
}

async function httpCall(method, endpoint, body = null, opts = {}) {
  // Gate on the resolved URL set (ACTIVE_SERVER_URL / SERVER_URLS), not the raw
  // primary SERVER_URL — a config with only *_FALLBACK_URLS leaves SERVER_URL
  // empty but still yields a usable ACTIVE_SERVER_URL, and httpCall iterates the
  // full SERVER_URLS list below.
  if (!ACTIVE_SERVER_URL) return null;
  // opts.timeoutMs lets long-poll claim calls hold longer than the default without aborting.
  const callTimeoutMs = Math.max(1, Number(opts.timeoutMs) || HTTP_TIMEOUT_MS);
  const options = { method, headers: {} };
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let lastError;
  for (const baseUrl of uniqueServerUrls([ACTIVE_SERVER_URL, ...SERVER_URLS])) {
    const url = `${baseUrl}/api/v1${endpoint}`;
    // ONE DESTINATION, ONE KEY: a failover to another URL must not carry this endpoint's credential.
    const key = keyFor(baseUrl);
    if (key) options.headers["X-API-Key"] = key; else delete options.headers["X-API-Key"];
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), callTimeoutMs);
    try {
  // NEVER FOLLOWED: `fetch` re-sends headers on a redirect, so a 302 hands the key to whatever it
  // points at. A 3xx fails `res.ok` like any other non-2xx. See aify-service-endpoint.mjs.
      const res = await fetch(url, { ...options, redirect: "manual", signal: controller.signal });
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
        lastError = new Error(`HTTP ${method} ${endpoint} timed out after ${callTimeoutMs}ms`);
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

// Built by runChannel(): the producer's instructions name the source its events arrive under, which depends on the entry.
let mcp = null;

/** The channel's MCP server, as the session's producer; with the local file tools when it holds the residency. */
function producerServer(fromPlugin, withFileTools) {
  const server = new Server(
    { name: "aify-comms-channel", version: AIFY_VERSION },
    {
      capabilities: {
        experimental: { "claude/channel": {} },
        ...(withFileTools ? { tools: {} } : {}),
      },
      instructions:
        `Events from aify resident dispatch arrive as <channel source="${channelSource(fromPlugin)}" ...>. ` +
        "These are real wake-up events for the current session. Handle them directly in this session. " +
        "Use the existing comms_* tools to coordinate and reply. " +
        "When a dispatch event includes Message ID, include that same value as inReplyTo when you reply so the run can close automatically. " +
        "If a reply is requested, send it in the SAME turn before you end — a managed session is not re-woken to finish a deferred reply, so a reply deferred to a later turn will strand. " +
        "Tag attributes: from_agent is the sender (the address to reply to), message_id is the inReplyTo value, run_id is the run the reply closes, and event_type=control is an interrupt or steer for a run already in progress. " +
        `A message whose from_agent is not "dashboard" comes from another agent: ${TRUST_RULE}`,
    },
  );
  if (withFileTools) {
    server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: SHARED_FILE_TOOLS }));
    server.setRequestHandler(CallToolRequestSchema, async ({ params }) => callSharedFileTool(params.name, params.arguments, {
      agentId: readBoundAgentId(), serverUrl: ACTIVE_SERVER_URL, apiKey: keyFor(ACTIVE_SERVER_URL),
    }));
  }
  return server;
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


// isChannelRun + the two receipt writers moved to ./channel-dispatch-receipts.mjs in v0.6 Phase 1.
// They were three of the eleven functions in this file no test had ever called. The transport is
// injected because this module has its OWN httpCall — see that file's header.
const { markDispatchDelivered, markDispatchDeliveryFailed } = makeDispatchReceipts({ httpCall });


// B3 (visible-TUI): orphan-sidecar self-exit guard. The channel sidecar runs as
// a child of the managed claude.exe. On Windows a child does NOT die with its
// parent, so if the controlling claude.exe (and its visible console PTY) dies
// but this sidecar survives, the agent keeps "beating" headless — a visible-TUI
// violation and a proliferation source. This guard detects the gone parent and
// self-exits so the worker truly disappears.
//
// CONSERVATIVE by construction: it only acts when there was a real original
// parent pid (>1) that is now reliably dead across CONSECUTIVE checks. A healthy
// sidecar (parent alive) never trips it; a transient process.kill error is
// tolerated by the consecutive-miss counter.
// Sped up 2026-06-05 ("never leave stuff behind"): 30s×3 ≈ 90s left a headless
// sidecar lingering long enough for the operator to see "available, no console"
// and for a dispatch to land on the orphan (delivered-no-reply). 3s×2 ≈ 6s still
// tolerates one transient process.kill miss but reaps the orphan promptly. We poll
// the ORIGINAL parent pid, so reparenting after claude dies doesn't hide it.
const PARENT_GUARD_CHECK_MS = 3_000;
const PARENT_GUARD_MISS_THRESHOLD = 2; // ~6s of consecutive dead-parent reads

// True when the sidecar's original controlling parent is gone, so a surviving
// orphan sidecar should self-exit. Conservative: an unknown/rootless ppid
// (0/1/undefined) NEVER self-kills.
export function parentIsGone({ originalPpid, isAlive } = {}) {
  if (!originalPpid || originalPpid <= 1) return false; // unknown/rootless → never self-kill
  if (typeof isAlive !== "function") return false;
  return !isAlive(originalPpid);
}

// Consecutive-miss latch: only act once we have seen the parent dead for
// `threshold` checks in a row. Exported for unit testing.
export function shouldSelfExit(misses, threshold = PARENT_GUARD_MISS_THRESHOLD) {
  return Number(misses) >= Number(threshold);
}

// Liveness-beat gate (status-liveness fix 2026-06-01). The sidecar's
// unconditional liveness beat keeps the server seeing the agent `online` purely
// because this process exists. If the controlling claude.exe died but the
// sidecar survives (orphan), the slow self-exit guard above takes ~90s to act,
// during which the orphan keeps beating -- so a dead RESIDENT shows online for
// up to ~90s + the server's stale window. Gate the beat itself: skip posting
// immediately when the original parent is known (>1) and provably dead. A
// transient false read just skips ONE beat (resumes next tick); a real dead
// parent makes the agent go stale faster, which is correct. Conservative on an
// unknown/rootless ppid (0/1/undefined) -- never skips. Injected isAlive for
// unit testing; production passes the real pidIsAlive.
export function shouldSkipBeatForDeadParent(ppid, isAlive) {
  if (!ppid || ppid <= 1) return false; // unknown/rootless -> always beat
  if (typeof isAlive !== "function") return false; // conservative: beat
  return !isAlive(ppid);
}

// pid liveness via signal 0. EPERM means the pid exists but we lack permission
// (still alive); ESRCH/anything else means gone. Conservative on ambiguity.
function pidIsAlive(pid) {
  if (!pid || pid <= 1) return true; // treat unknown as alive → never trip the guard
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

async function pollLoop(onParentGone) {
  // Capture the original controlling parent pid ONCE (the managed claude.exe),
  // up front so both the liveness-beat gate (below) and the self-exit guard
  // (further down) read the same value.
  const ORIGINAL_PPID = process.ppid;
  const stopLiveness = startLivenessHeartbeat({
    intervalMs: 30_000,
    beat: async () => {
      if (!ACTIVE_SERVER_URL) return;
      const id = readBoundAgentId();
      if (!id) return;
      // Orphan-sidecar liveness gate: if our controlling claude.exe is gone,
      // stop beating immediately instead of misleading the server into showing
      // a dead resident as online until the slow self-exit guard fires.
      if (shouldSkipBeatForDeadParent(ORIGINAL_PPID, pidIsAlive)) return;
      await httpCall("POST", `/agents/${encodeURIComponent(id)}/heartbeat`, {
        bridgeId: channelBridgeId(id),
        bridgeKind: "channel-sidecar",
        liveness: true,
        bridgeBuild: BRIDGE_BUILD_TAG,
      });
    },
  });

  // B3: orphan-sidecar self-exit guard. Capture the original controlling parent
  // pid ONCE (the managed claude.exe). A periodic, unref'd check self-exits only
  // after the parent is seen dead for PARENT_GUARD_MISS_THRESHOLD consecutive
  // ticks — never on a transient read or for a healthy sidecar.
  // (ORIGINAL_PPID was captured at the top of pollLoop, shared with the gate.)
  let parentMisses = 0;
  let parentGuardTimer = null;
  const stopParentGuard = () => {
    if (parentGuardTimer) { clearInterval(parentGuardTimer); parentGuardTimer = null; }
  };
  if (ORIGINAL_PPID && ORIGINAL_PPID > 1) {
    parentGuardTimer = setInterval(() => {
      if (parentIsGone({ originalPpid: ORIGINAL_PPID, isAlive: pidIsAlive })) {
        parentMisses += 1;
      } else {
        parentMisses = 0; // reset on any live (or transient) observation
        return;
      }
      if (shouldSelfExit(parentMisses, PARENT_GUARD_MISS_THRESHOLD)) {
        console.error(
          `[claude-channel] controlling parent ${ORIGINAL_PPID} gone — orphan sidecar self-exiting`,
        );
        stopParentGuard();
        stopLiveness();
        onParentGone();
      }
    }, PARENT_GUARD_CHECK_MS);
    if (typeof parentGuardTimer.unref === "function") parentGuardTimer.unref();
  }
  try {
  while (true) {
    try {
      if (!ACTIVE_SERVER_URL) {
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
          // Long-poll only the first claim of the batch; the rest drain queued runs.
          waitMs: (i === 0 ? CLAIM_WAIT_MS : 0),
        }, (i === 0 ? CLAIM_OPTS : {}));
        // `stopped` is reversible. Stay dormant rather than killing this MCP
        // process: for a resident Claude session this is the only push channel,
        // and re-registering the still-live session cannot restart a dead child.
        // Managed parents are still reaped server-side; the parent guard then
        // cleans up their dormant sidecar.
        if (claim?.stopped) {
          console.error(
            `[claude-channel] agent '${agentId}' is stopped (disabled); dormant until re-registered`,
          );
          await sleep(RELEASE_RECHECK_MS);
          batch.length = 0;
          break;
        }
        // Mode FSM release signal (Task 4.1): the operator switched this agent
        // to resident, so this managed sidecar is no longer the driver.
        // DORMANT, NOT DEAD (2026-06-12, sc-manager strand): the old permanent
        // `return` killed the poll loop forever — but for a resident claude this
        // sidecar IS the session's only delivery path, and a transient release
        // (the mode switch briefly clobbering driver_state before the server's
        // adopt-live-resident self-heal) permanently severed delivery: sends said
        // "sent", runs queued, nothing claimed until a full session restart. Go
        // dormant for one back-off window and re-check — the next claim either
        // gets released again (still displaced → stay dormant) or resumes
        // normally (the server healed driver_state → we are the driver).
        if (claim?.release) {
          console.error(
            `[claude-channel] released: agent '${agentId}' not the active driver; dormant for ${Math.round(RELEASE_RECHECK_MS / 1000)}s, then re-checking.`,
          );
          await sleep(RELEASE_RECHECK_MS);
          batch.length = 0;
          break;
        }
        const executionMode = String(claim?.run?.executionMode || "").trim().toLowerCase();
        if (!claim?.run || !["channel", "resident"].includes(executionMode)) break;
        batch.push(claim.run);
      }
      if (batch.length === 1) {
        const run = batch[0];
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
      } else if (batch.length > 1) {
        const combined = batch.map((run, i) => `--- Message ${i + 1} of ${batch.length} ---\n${dispatchContent(agentId, run)}`).join("\n\n");
        const highestPriority = batch.some(r => r.priority === "urgent") ? "urgent" : batch.some(r => r.priority === "high") ? "high" : "normal";
        try {
          await emitChannel(combined, {
            event_type: "dispatch_batch",
            agent_id: agentId,
            count: batch.length,
            priority: highestPriority,
          });
        } catch (error) {
          // The DELIVERY itself failed → none reached the agent → fail all.
          for (const run of batch) {
            await markDispatchDeliveryFailed(run.id, error);
          }
          throw error;
        }
        // Delivery succeeded — every run reached the agent. Mark each; a per-run PATCH
        // failure must fail ONLY that run, not flip already-delivered siblings to
        // [FAILED] and send the sender a spurious failure handoff (bughunt 2026-07-03).
        for (const run of batch) {
          try {
            await markDispatchDelivered(run);
          } catch (e) {
            await markDispatchDeliveryFailed(run.id, e);
          }
        }
      }

      // Poll for controls (interrupt/steer) independently of run tracking, so the sender uses
      // the same tool regardless of target runtime.
      //
      // THIS NAMED `comms_run_steer` UNTIL 2026-09-09 AND THAT TOOL NO LONGER EXISTS -- it was
      // removed from the stdio bridge (see `register-tools.mjs`) and from the SSE transport,
      // each with its own note. The MECHANISM is live and was verified rather than assumed:
      // `dispatch_runs.py` still writes `action="steer"` when an ordinary `comms_send` steers a
      // busy steer-capable target, and this loop is what delivers it. So the comment named a
      // tombstone as though it were a caller, which is the shape a reader cannot tell from a
      // live one.
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
          // Says "channel", not "resident", and says CONTROL, not delivered (v0.2.0).
          // This is a control (interrupt/steer) completion receipt, emitted by the channel
          // sidecar for BOTH resident and managed claude agents — the loop above does not
          // branch on session_mode. The old wording claimed a resident session, and the
          // controls API surfaces `response_text` back to callers verbatim
          // (verified in api_v2.py when this was written; that file is now 53 lines and the controls
          // surface lives in service/routers/dispatch_messages/controls.py), so a MANAGED agent's control read back as
          // "Delivered to Claude resident session".
          //
          // That is not cosmetic: it cost a real diagnosis. It was read as evidence that
          // managed WORK takes the resident delivery path, which became the leading
          // hypothesis for the still-open restart bug and gated a v0.2 workstream on it.
          // Actual brief delivery never says this — `markDispatchDelivered` writes
          // "Delivered to Claude channel bridge" with an empty summary. A receipt that
          // names the wrong session mode is a trap for the next reader.
          response: "Control emitted into the Claude channel session",
          // The service refuses an actor-less settlement (2026-08-18) and a refused control stays
          // pending forever, stranding its run — so these two fields are not optional metadata, they
          // are what keeps this receipt working. Both are already in scope from the claim above.
          handledBy: agentId,
          machineId: MACHINE_ID,
        });
      }
    } catch (error) {
      console.error("[aify-channel] tick error:", error?.message || String(error));
    }

    await sleep(POLL_MS);
  }
  } finally {
    stopParentGuard();
    stopLiveness();
  }
}

/**
 * Run the channel: the user-scope server's entry (this file) or the plugin's (claude-channel-plugin.mjs). Everything
 * with an effect outside this process starts here and nowhere else, so importing this module starts nothing.
 *
 * A server that is not this route's producer (channel-producer.mjs) answers Claude as an MCP server with no channel
 * and no instructions, and does nothing else: no runtime marker, no claim, no heartbeat. With nothing else holding it,
 * the process ends when Claude closes its stdin.
 */
export async function runChannel({ fromPlugin = false } = {}) {
  if (!isThisRoutesProducer({ fromPlugin, route: process.env.AIFY_CHANNEL_ROUTE })) {
    mcp = new Server({ name: "aify-comms-channel", version: AIFY_VERSION }, { capabilities: {} });
    await mcp.connect(new StdioServerTransport());
    return;
  }
  const holdsResidency = holdsResidentDuties();
  mcp = producerServer(fromPlugin, holdsResidency);
  // Write our claude-code runtime marker from this long-lived bridge process.
  // This must happen here, not in the wrapper's bash CLI call, because on
  // Git Bash for Windows `$$` is an MSYS shell PID and isProcessAlive() from
  // node cannot see it — listRuntimeMarkers would auto-delete the wrapper's
  // marker on first read. node's process.pid is a real Windows PID.
  try {
    writeRuntimeMarker("claude-code", MARKER_CWD, {
      channelEnabled: true,
      parentPid: process.ppid || "",
    });
  } catch (error) {
    console.error("[aify-channel] failed to write runtime marker:", error?.message || String(error));
  }
  // Started after the connection, so Claude's MCP start-up never waits on a registration (resident-duties.mjs).
  let residency = null;
  let leaving = false;
  // Every way out takes this once: a signal, Claude closing stdin, or the parent gone.
  const leave = async (code) => {
    if (leaving) return;
    leaving = true;
    await residency?.end();
    removeOwnMarker();
    process.exit(code);
  };
  process.on("exit", () => { removeOwnMarker(); residency?.dropBinding(); });
  process.on("SIGINT", () => { leave(130); });
  process.on("SIGTERM", () => { leave(143); });
  // A closed terminal window sends SIGHUP, whose default exits without running any handler.
  process.on("SIGHUP", () => { leave(129); });
  await mcp.connect(new StdioServerTransport());
  process.stdin.once("end", () => { leave(0); });
  if (holdsResidency) residency = new ResidentDuties().start();
  pollLoop(() => leave(0)).catch((error) => {
    console.error("[aify-channel] fatal:", error);
    process.exit(1);
  });
}

// IS_MAIN only tells this file's own start (the user-scope server) from an import.
if (IS_MAIN) await runChannel({ fromPlugin: false });
