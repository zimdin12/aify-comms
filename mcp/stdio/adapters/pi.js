import { RuntimeAdapter } from "./base.js";

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
    // Plan 2 pi flip: resident pi is no longer supported.
    // Tasks 7-11 will wire managed mode to PiController.
    return null;
  }
}
