import { RuntimeAdapter } from "./base.js";

// Defer controller resolution to break the module-load cycle:
//   adapters/opencode.js -> controllers/opencode-controller.js ->
//   runtimes.js -> adapters/index.js -> adapters/opencode.js
// A static `import { OpencodeController }` deadlocks `OpencodeAdapter`'s
// class binding during index.js evaluation. controllerFor() is called at
// dispatch time, long after all top-level evaluation completes, so a
// dynamic import resolved on first use is safe.
let _OpencodeControllerCtor = null;
let _resolvePromise = null;
function ensureControllerResolved() {
  if (_OpencodeControllerCtor) return Promise.resolve(_OpencodeControllerCtor);
  if (!_resolvePromise) {
    _resolvePromise = import("../controllers/opencode-controller.js").then((mod) => {
      _OpencodeControllerCtor = mod.OpencodeController;
      return _OpencodeControllerCtor;
    });
  }
  return _resolvePromise;
}

// Kick off resolution at module-load (fire-and-forget); errors surface from
// controllerFor() if the eager warm-up failed.
ensureControllerResolved().catch(() => {});

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
    if (!_OpencodeControllerCtor) {
      // Eager warm-up didn't complete (unlikely once dispatch runs).
      // Fall back to synchronous-style throw so the failure is visible
      // instead of silently returning null.
      throw new Error(
        "OpencodeController not yet resolved. Call adapterFor('opencode').warmup() before dispatch, " +
          "or await controllerForAsync(opts).",
      );
    }
    return new _OpencodeControllerCtor(opts);
  }

  // Test/integration helper — awaits controller resolution.
  async warmup() {
    await ensureControllerResolved();
  }
}
