// How an agent's runtime is described, and — the substantial part — HOW IT CAN BE WOKEN.
//
// `wakeModeSummary` is a fourteen-branch classifier answering the question every dispatch decision rests
// on: given this agent's runtime, session mode, capabilities, handle and runtime config, what is the
// mechanism by which work reaches it? Its answers are what an operator reads in `comms_agents` and
// `comms_agent_info`, and what tells them whether a silent agent is idle or structurally unreachable.
//
// v0.5.4 layer 0 of the server.js decomposition. Both functions lived in `server.js`, the bin entry point,
// which nothing imports — so fourteen ordered branches deciding deliverability had no test at all.
//
// THE BRANCH ORDER IS LOAD-BEARING, which is the reason this needed extracting more than it needed
// relocating. Several conditions overlap: a resident codex agent with a handle AND a live app-server is
// `codex-live`, and the SAME agent without the app-server is `codex-thread-resume` — the second condition
// is a strict subset of the first, so swapping the two branches would silently downgrade every live codex
// agent to the fallback path. The same shape holds for hermes. `agent-summary.test.js` pins each
// overlapping pair in both directions.
//
// `MACHINE_ID` is bound here the way `claude-channel.js`, `hermes-channel.js`, `hermes-env.mjs` and
// `hermes-managed-host.js` each bind it: by calling the one `defaultMachineId()` in `runtimes.js`. That is
// a repeated derivation of a pure function, not a second owner — all five agree by construction, and
// there is nothing to keep in sync.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { defaultMachineId, hasCodexLiveAppServer, normalizeRuntime } from "./runtimes.js";
import { parseJson } from "./parse-json.mjs";
import { normalizeSessionMode } from "./session-mode.mjs";

const MACHINE_ID = defaultMachineId();

export function runtimeSummary(info = {}) {
  const runtime = normalizeRuntime(info.runtime || "generic");
  const machine = info.machineId || info.machine_id || MACHINE_ID;
  const sessionMode = normalizeSessionMode(info.sessionMode || info.session_mode);
  return `${runtime} @ ${machine} (${sessionMode})`;
}

export function wakeModeSummary(info = {}) {
  const explicit = String(info.wakeMode || "").trim();
  if (explicit) return explicit;
  const runtime = normalizeRuntime(info.runtime || "generic");
  const sessionMode = normalizeSessionMode(info.sessionMode || info.session_mode);
  const capabilities = Array.isArray(info.capabilities) ? info.capabilities : [];
  if (sessionMode === "managed" && capabilities.includes("managed-run")) return "managed-worker";
  if (sessionMode === "resident" && runtime === "claude-code" && capabilities.includes("resident-run")) return "claude-live";
  if (
    sessionMode === "resident" &&
    runtime === "codex" &&
    capabilities.includes("resident-run") &&
    info.sessionHandle &&
    hasCodexLiveAppServer(parseJson(info.runtimeConfig, {}))
  ) {
    return "codex-live";
  }
  if (sessionMode === "resident" && runtime === "codex" && capabilities.includes("resident-run") && info.sessionHandle) return "codex-thread-resume";
  if (
    sessionMode === "resident" &&
    runtime === "hermes" &&
    capabilities.includes("resident-run") &&
    /^wss?:\/\//i.test(String(parseJson(info.runtimeConfig, {})?.gatewayUrl || ""))
  ) {
    // Legacy gateway-channel resident hermes status. NOTE (2026-05-30
    // hermes-apiserver-delivery): the tui_gateway WS-bind delivery path this
    // status described was retired (HermesResidentController +
    // aify.session.bind_transport deleted). Managed/resident hermes now delivers
    // via the hermes-channel.js api_server sidecar. This branch is left for the
    // install.sh + service-status rewrite (plan Tasks D/E) to supersede.
    return "hermes-live";
  }
  if (sessionMode === "resident" && runtime === "opencode" && capabilities.includes("resident-run") && info.sessionHandle) return "opencode-session-resume";
  if (sessionMode === "resident" && runtime === "pi" && capabilities.includes("resident-run") && info.sessionHandle) return "pi-session-resume";
  if (sessionMode === "resident" && runtime === "codex" && !info.sessionHandle) return "codex-missing-handle";
  // hermes deliverability is keyed on the GATEWAY, never the handle (resident-run
  // / hermes-live require a live ws:// gateway). The handle is now the agent's
  // REAL hermes session id (native-session-id model, 2026-06-03), recorded like
  // any other runtime, so this diagnostic must key on the gateway alone,
  // mirroring the service (api_v2.py).
  if (sessionMode === "resident" && runtime === "hermes" && !/^wss?:\/\//i.test(String(parseJson(info.runtimeConfig, {})?.gatewayUrl || ""))) return "hermes-missing-handle";
  if (sessionMode === "resident" && runtime === "opencode" && !info.sessionHandle) return "opencode-missing-handle";
  if (sessionMode === "resident" && runtime === "pi" && !info.sessionHandle) return "pi-missing-handle";
  if (sessionMode === "resident" && runtime === "claude-code") return "claude-needs-channel";
  return "message-only";
}
