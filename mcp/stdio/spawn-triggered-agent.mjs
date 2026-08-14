// Cold-starting an agent because a message arrived for it.
//
// Extracted from server.js in v0.5.4. `comms_send` and `comms_channel_send` call this when the target is
// MANAGED and resting at `available` with no live worker: the send is what wakes it. Both callers ignore
// the return value, and every path here — success and every skip — returns undefined, so failure is
// reported by logging rather than to the caller. That is deliberate: a send must not fail because a
// cold-start did.
//
// `LOCAL_RUNTIME_STATE` MOVES WITH IT. docs/JS_SPAWN_TRIGGERED_AGENT_PACKET.md recorded the Map as an
// UNOWNED server.js dependency with four references, and the reviewer ruled on that basis that it stays in
// server.js. Measured again before this move: all three of its uses are inside this function, so by the
// ownership test used throughout the series — count the DIRECT readers of a mutable module-scope name, and
// if they are all inside the group, the group owns it — the premise has changed. Its other users left in
// earlier slices.
//
// IN-MEMORY WINS OVER PERSISTED, and the packet flagged this as a decision rather than an accident:
// `{ ...baseState, ...(LOCAL_RUNTIME_STATE.get(targetId) || {}) }` treats the Map as fresher than the
// registry file, because a runtime that has reported state this process is more current than what was last
// written to disk.

import { randomUUID } from "crypto";

import { __markControllerStart } from "./controller-activity.mjs";
import { deliverMessage, readAgents, writeAgents } from "./local-store.mjs";
import { httpCall } from "./aify-service-endpoint.mjs";
import { normalizeSessionMode } from "./session-mode.mjs";
import { parseJson } from "./parse-json.mjs";
import { canLaunchRuntime, launchRuntimeRun, normalizeRuntime } from "./runtimes.js";

// Per-agent runtime state this process has been told about, fresher than the registry file.
export const LOCAL_RUNTIME_STATE = new Map();

export function spawnTriggeredAgent({ targetId, targetInfo, from, type, subject, body }) {
  const sessionMode = normalizeSessionMode(targetInfo.sessionMode);
  const runtime = normalizeRuntime(targetInfo.runtime || "generic");
  const capabilities = Array.isArray(targetInfo.capabilities) ? targetInfo.capabilities : [];
  const residentRunnable =
    sessionMode === "resident" &&
    runtime === "codex" &&
    capabilities.includes("resident-run") &&
    targetInfo.sessionHandle;
  const managedRunnable = sessionMode === "managed" && capabilities.includes("managed-run");
  if (!residentRunnable && !managedRunnable) {
    const reason =
      sessionMode === "resident"
        ? `Agent "${targetId}" is a resident session without a triggerable session handle. Re-register that live session first.`
        : `Agent "${targetId}" is not configured as a launchable managed session.`;
    deliverMessage(from, {
      id: `${Date.now()}-${randomUUID().slice(0, 8)}`,
      from: targetId,
      type: "error",
      subject: `[FAILED] ${subject}`,
      body: reason,
    });
    return;
  }
  if (!canLaunchRuntime(runtime)) {
    deliverMessage(from, {
      id: `${Date.now()}-${randomUUID().slice(0, 8)}`,
      from: targetId,
      type: "error",
      subject: `[FAILED] ${subject}`,
      body: `Runtime "${runtime}" does not support active dispatch`,
    });
    return;
  }

  const run = {
    id: `local-${Date.now()}-${randomUUID().slice(0, 8)}`,
    from,
    targetAgentId: targetId,
    type,
    subject,
    body,
    mode: "require_start",
    executionMode: residentRunnable ? "resident" : "managed",
  };
  const baseState = parseJson(targetInfo.runtimeState, {});
  const runtimeState = { ...baseState, ...(LOCAL_RUNTIME_STATE.get(targetId) || {}) };

  const controller = launchRuntimeRun({
    agentId: targetId,
    agentInfo: { ...targetInfo, runtime },
    run,
    runtimeState,
    callbacks: {
      // Plan 4 Task 13: same ready surface as the main dispatch loop.
      onReady: () => {
        httpCall("PATCH", `/agents/${encodeURIComponent(targetId)}/ready`, {
          ready: true,
          requestedBy: "controller-handshake",
        }).catch(() => { /* best-effort */ });
      },
      onRuntimeState: (nextState) => {
        const merged = { ...(LOCAL_RUNTIME_STATE.get(targetId) || {}), ...nextState };
        LOCAL_RUNTIME_STATE.set(targetId, merged);
        const registry = readAgents();
        if (registry.agents[targetId]) {
          registry.agents[targetId].runtimeState = merged;
          writeAgents(registry);
        }
      },
      onEvent: () => {},
      onRefs: () => {},
    },
  });
  // Plan 4 Task 13: track this controller's work promise so the turn-busy
  // heartbeat fires while it's unresolved.
  __markControllerStart(controller.promise);

  controller.promise
    .then(() => {})
    .catch((err) => {
      console.error("[aify] local triggered run failed:", err);
    });
}
