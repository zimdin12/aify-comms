import { RuntimeAdapter } from "./base.js";
import { PiController } from "../controllers/pi-controller.js";

export class PiAdapter extends RuntimeAdapter {
  get name() { return "pi"; }
  get displayName() { return "Pi"; }
  get sessionEnvVars() { return ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]; }

  // Plan 2 capability matrix — the pi delivery flip:
  //   resident=false because omp --mode rpc is single-client stdio.
  //   preferredDeliveryMode pins pi to the unified wrapper-backing path.
  get supportsResident() { return false; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return false; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  controllerFor(opts) {
    // Plan 2 pi flip: resident pi is no longer supported — return null
    // so launchRuntimeRun rejects with a clear error.
    const mode = String(
      opts?.executionMode ||
      opts?.run?.executionMode ||
      opts?.agentInfo?.sessionMode ||
      "managed",
    ).trim().toLowerCase();
    if (mode === "resident") return null;
    return new PiController(opts);
  }
}
