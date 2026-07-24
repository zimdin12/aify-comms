import { RuntimeAdapter } from "./base.js";
import { OpencodeController } from "../controllers/opencode-controller.js";

export class OpencodeAdapter extends RuntimeAdapter {
  get name() { return "opencode"; }
  get displayName() { return "OpenCode"; }
  get sessionEnvVars() { return ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]; }

  // The managed controller owns a per-run OpenCode server and injects ordinary
  // busy sends through the native promptAsync endpoint.
  get supportsResident() { return false; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return false; }
  get preferredDeliveryMode() { return "managed"; }

  // Symmetric adapter contract (Phase 2: every adapter advertises these).
  // ASYMMETRY(opencode): aify-comms does not wire `opencode serve` today, so
  // there is no live resident-attach path and no captured-id hook. The session
  // id (when present) is read from OPENCODE_SESSION_ID and would be fed back to
  // resume — hence sessionIdSource="resume". The takeover command is the
  // symmetric wrapper form (`opencode-aify --resume <id>`) for forward parity;
  // it is presence-only until serve is wired. Do NOT exercise opencode live.
  get sessionIdSource() { return "resume"; }

  resumeCommand(sessionId) {
    return `opencode-aify --resume ${sessionId}`;
  }

  controllerFor(opts) {
    const mode = String(opts?.executionMode || opts?.run?.executionMode || opts?.agentInfo?.sessionMode || "managed")
      .trim()
      .toLowerCase();
    if (mode === "resident") return null;
    if (mode !== "managed") return null;
    return new OpencodeController(opts);
  }
}
