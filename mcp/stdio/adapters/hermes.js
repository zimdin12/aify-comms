import { RuntimeAdapter } from "./base.js";

export class HermesAdapter extends RuntimeAdapter {
  get name() { return "hermes"; }
  get displayName() { return "Hermes"; }
  get sessionEnvVars() { return ["HERMES_SESSION_ID", "HERMES_SESSION"]; }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_HERMES_GATEWAY_URL = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim() || "(unset)";
    return env;
  }
}
