import { RuntimeAdapter } from "./base.js";
import { CodexController } from "../controllers/codex-controller.js";

export class CodexAdapter extends RuntimeAdapter {
  get name() { return "codex"; }
  get displayName() { return "Codex"; }
  get sessionEnvVars() { return ["CODEX_THREAD_ID"]; }

  // Plan 2 capability matrix
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  controllerFor(opts) {
    return new CodexController(opts);
  }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_CODEX_APP_SERVER_URL = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim() || "(unset)";
    return env;
  }
}
