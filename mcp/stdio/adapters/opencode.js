import { RuntimeAdapter } from "./base.js";
import { OpencodeController } from "../controllers/opencode-controller.js";

export class OpencodeAdapter extends RuntimeAdapter {
  get name() { return "opencode"; }
  get displayName() { return "OpenCode"; }
  get sessionEnvVars() { return ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]; }

  // Plan 2 capability matrix. aify-comms doesn't wire `opencode serve`
  // today — capabilities describe current aify-comms delivery surface.
  // Wiring serve is tracked as separate follow-up.
  get supportsResident() { return false; }
  get supportsManaged() { return true; }
  get supportsSteering() { return false; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return false; }
  get preferredDeliveryMode() { return "managed"; }

  controllerFor(opts) {
    const mode = String(opts?.executionMode || opts?.run?.executionMode || opts?.agentInfo?.sessionMode || "managed")
      .trim()
      .toLowerCase();
    if (mode !== "managed" && mode !== "resident") return null;
    return new OpencodeController(opts);
  }
}
