import { RuntimeAdapter } from "./base.js";
import { HermesController } from "../controllers/hermes-controller.js";
import { pinnedSessionId } from "../hermes-session-id.js";

export class HermesAdapter extends RuntimeAdapter {
  get name() { return "hermes"; }
  get displayName() { return "Hermes"; }
  get sessionEnvVars() { return ["HERMES_SESSION_ID", "HERMES_SESSION"]; }

  // Capability matrix — api_server delivery model (2026-05-30
  // hermes-apiserver-delivery plan). Managed hermes delivers like claude-aify:
  // the per-agent hermes-channel.js sidecar claims dispatch runs over HTTP and
  // drives the agent's pinned api_server session (POST /api/sessions/{id}/
  // chat/stream). This is the "channel" model — the dead tui_gateway WS bind
  // (aify.session.bind_transport) has been retired.
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  // No mid-turn steer over the api_server chat path: a chat/stream turn runs to
  // completion. The /v1/runs/{id}/stop endpoint gives interrupt only. Advertise
  // steer=false so callers don't promise a capability the transport can't honor.
  get supportsSteering() { return false; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  controllerFor(opts) {
    return new HermesController(opts);
  }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_HERMES_APISERVER_URL = String(process.env.AIFY_HERMES_APISERVER_URL || "").trim() || "(unset)";
    env.AIFY_HERMES_APISERVER_KEY = process.env.AIFY_HERMES_APISERVER_KEY ? "(set)" : "(unset)";
    return env;
  }

  // Session-id truth (2026-05-30 hermes-apiserver-delivery): a managed hermes
  // agent's session is the STABLE per-agent api_server session id derived from
  // its OWN agentId (pinnedSessionId). The hermes-channel.js sidecar pins and
  // drives exactly this session, so the adapter must report the byte-identical
  // value.
  //
  // NEVER return the gateway global session.most_recent: that reads hermes'
  // shared global state and cross-contaminates managed agents that share a
  // gateway (#135). agentId precedence: explicit opts.agentId, then env
  // AIFY_AGENT_ID / AIFY_COMMS_AGENT_ID. No agentId → null (no machine-global
  // guess). Injectable via opts for tests.
  async discoverSessionId(opts = {}) {
    const { env = process.env, agentId } = opts;
    const resolvedAgentId = String(
      agentId || env.AIFY_AGENT_ID || env.AIFY_COMMS_AGENT_ID || "",
    ).trim();
    if (!resolvedAgentId) return null;
    return pinnedSessionId(resolvedAgentId);
  }
}
