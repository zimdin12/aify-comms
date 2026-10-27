// The resident duties of a Claude agent's local comms process, held by the Claude channel (0.9.11 slice 3).
//
// Until 0.9.11 the tool bridge (`server.js`) did these beside the channel. When a launch's comms tools come over
// HTTP from the service, the channel is Claude's only local comms process, so it takes them: start-up
// registration (`auto-registration.mjs`, reused with its retry and its same-session, tombstone and managed
// guards), the owner's liveness beat, the session-handle beat, and on the way out resident-lost and the
// binding file's removal.
//
// TWO IDS, ONE PER ROLE, as the bridge and the channel had them. The OWNER id is this process's
// `BRIDGE_INSTANCE_ID`: registration, the binding file, the handle beat and resident-lost all carry it, and the
// service and `removeAgentBindingFile` act on a leaving process only when its owner id still matches. So a
// channel that exits after its successor registered changes nothing. The channel's claims and sidecar beat
// keep `channelBridgeId(agentId)`, the row the service's sidecar rules are written for.
//
// Only a launch that says its tools come over HTTP (`AIFY_COMMS_MCP=http`, set by the launcher) gets this: a
// launch still running `server.js` has it doing the same duties, and two owners would refuse each other.

import { API_KEY, IS_REMOTE, SERVER_URL, httpCall } from "./aify-service-endpoint.mjs";
import { makeAutoRegister } from "./auto-registration.mjs";
import { removeAgentBindingFile } from "./binding-file.js";
import { BRIDGE_BUILD_TAG } from "./bridge-build.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
import { __HEARTBEAT_MS } from "./poll-intervals.mjs";
import { reportResidentLost } from "./resident-lost.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";
import { makeDefaultHandlePoster, startSessionHandleHeartbeat } from "./session-handle-heartbeat.js";

/** How long a leaving channel waits for resident-lost, so an unreachable service never holds the exit. */
const RESIDENT_LOST_BOUND_MS = 1500;

/** Whether this launch's channel holds the resident duties. An RPC child (`AIFY_BRIDGE_DISABLED=1`) never does. */
export function channelHoldsResidency(env = process.env) {
  return String(env.AIFY_COMMS_MCP || "").trim().toLowerCase() === "http"
    && String(env.AIFY_BRIDGE_DISABLED || "").trim() !== "1";
}

export class ChannelResidency {
  constructor({
    agentId = AIFY_AGENT_ID,
    ownerId = BRIDGE_INSTANCE_ID,
    env = process.env,
    call = httpCall,
    register = makeAutoRegister({ ensureDispatchLoop: () => {} }),
    removeBinding = removeAgentBindingFile,
    boundMs = RESIDENT_LOST_BOUND_MS,
  } = {}) {
    Object.assign(this, { agentId, ownerId, env, call, register, removeBinding, boundMs });
    this.stops = [];
    this.ended = null;
  }

  /** Register, then beat as the owner and report the session handle while the process lives. */
  start() {
    void this.register().catch(() => {});
    if (!IS_REMOTE || !this.agentId) return this;
    this.stops.push(startLivenessHeartbeat({
      intervalMs: 30_000,
      beat: () => this.call("POST", `/agents/${encodeURIComponent(this.agentId)}/heartbeat`, {
        bridgeId: this.ownerId, bridgeKind: "resident", liveness: true, bridgeBuild: BRIDGE_BUILD_TAG,
      }),
    }));
    this.stops.push(startSessionHandleHeartbeat({
      adapter: __runtimeAdapter,
      agentId: this.agentId,
      intervalMs: __HEARTBEAT_MS,
      postFn: makeDefaultHandlePoster(SERVER_URL, API_KEY, this.ownerId),
    }));
    return this;
  }

  /** Once, however many signals arrive: stop beating, report resident-lost within the bound, drop the binding. */
  end() {
    this.ended ??= (async () => {
      for (const stop of this.stops) {
        try { stop(); } catch { /* best effort */ }
      }
      await Promise.race([
        reportResidentLost({
          httpCall: this.call,
          agentId: this.agentId,
          bridgeId: this.ownerId,
          sessionMode: this.env.AIFY_SESSION_MODE,
          runtime: this.env.AIFY_RUNTIME || "claude-code",
        }).catch(() => false),
        new Promise((resolve) => setTimeout(resolve, this.boundMs).unref?.()),
      ]);
      this.dropBinding();
    })();
    return this.ended;
  }

  /** Synchronous, for `process.on("exit")`: only the binding this process wrote. */
  dropBinding() {
    try { this.removeBinding({ pid: process.ppid || process.pid, bridgeId: this.ownerId }); } catch { /* best effort */ }
  }
}
