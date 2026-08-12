// Reporting a managed run's state back to aify, and the bridge identity those reports are filed under.
//
// Six functions, 49 lines, extracted from `hermes-managed-host.js` in v0.5.4 BEFORE the delivery loop that
// calls them. That order is deliberate and the reviewer's: the loop is the live dispatch path, and giving the
// surface it reports through an owner first means the loop's own slice has less to prove.
//
// EVERY ONE TAKES `httpCall` AS A PARAMETER. That is why this cluster is 49 lines and not 300 — the HTTP
// mechanism is injected by the caller, so none of these functions owns a client, a base URL, or a retry
// policy. It is also why the closure came out at exactly the six seeds with nothing dragged in, and why the
// tests below can assert what gets sent without a server: pass a recorder and read the calls.
//
// WHAT THE DISTINCTIONS MEAN, because they are the reason these are six functions and not one `report()`:
//   markRunDelivered   routine delivery, summary deliberately EMPTY so the Runs audit view stays clean
//   markRunFailed      carries a real summary — failures are where a summary earns its place
//   markRunRequeued    the run goes back on the queue; nothing was attempted, so it is not a failure
//   reportTurnBusy     the agent is mid-turn; this is what stops a second dispatch landing on it
//   clearTurn          the turn ended; without this the busy flag is what strands the next dispatch
// A run reported failed when it should have been requeued loses work that was never tried, which is the
// distinction the whole requeue-versus-fail rule on the Python side exists to protect.
//
// `CHANNEL_BRIDGE_PREFIX` follows `channelBridgeId`, its only reader. Note the value embeds the old module's
// name — `hermes-managed-host-${MACHINE_ID}` is the identity this bridge registers and heartbeats under, so
// it is a WIRE VALUE and not a label: renaming it to match this file would orphan every live bridge row.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run and the wrappers relaunch.

import { MACHINE_ID, RUNTIME } from "./hermes-env.mjs";

export const CHANNEL_BRIDGE_PREFIX = `hermes-managed-host-${MACHINE_ID}`;


export function channelBridgeId(agentId) {
  const id = String(agentId || "").trim();
  return id ? `${CHANNEL_BRIDGE_PREFIX}-${id}` : CHANNEL_BRIDGE_PREFIX;
}


export async function reportTurnBusy(httpCall, agentId, { busy, runId = "" } = {}) {
  await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/heartbeat`, {
    bridgeId: channelBridgeId(agentId),
    turnBusy: !!busy,
    turnRunId: runId,
    turnRuntime: RUNTIME,
  });
}


export async function clearTurn(httpCall, agentId) {
  await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/turn-end`, {
    bridgeId: channelBridgeId(agentId),
    turnRuntime: RUNTIME,
  });
}


export async function markRunDelivered(httpCall, run) {
  const runId = String(run?.id || "");
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "delivered",
    // D2 (#162): routine delivery is normal-path — no summary so the Runs audit
    // view stays clean. The 'delivered' event below carries the audit signal;
    // meaningful summaries are reserved for failures (see markRunFailed).
    summary: "",
    runtime: RUNTIME,    appendEvent: "Delivered to managed-hermes visible TUI (agent self-replies)",
    eventType: "delivered",
  });
}


export async function markRunFailed(httpCall, run, error) {
  const runId = String(run?.id || "");
  const cause = error?.message || String(error);
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "failed",
    error: cause,
    summary: `managed hermes delivery failed: ${cause}`,
    runtime: RUNTIME,    appendEvent: `managed hermes delivery failed: ${cause}`,
    eventType: "failed",
  });
}


export async function markRunRequeued(httpCall, run, reason) {
  const runId = String(run?.id || "");
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "queued",
    runtime: RUNTIME,    appendEvent: `managed hermes delivery deferred (requeued): ${reason}`,
    eventType: "requeued",
  });
}
