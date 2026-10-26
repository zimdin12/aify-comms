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

  // Symmetric adapter contract (Phase 2: every adapter advertises these).
  // codex session ids come from a prior rollout that aify RESUMES by handing
  // the id back to the CLI — internally the codex-aify wrapper runs
  // `codex resume --include-non-interactive <id>` (see install.sh codex
  // branch). The load-bearing part is the POSITIONAL <id>: that is what
  // resumes the rollout. `--include-non-interactive` is a no-op when an
  // explicit id is given (it only matters for id-less resume), kept only for
  // parity with the interactive path. The operator takeover command is the
  // wrapper form.
  // ASYMMETRY(codex): the wrapper rewrites `--resume <id>` into the codex
  // `resume` subcommand; the operator-facing command stays the symmetric
  // `<wrapper> --resume <id>` form.
  get sessionIdSource() { return "resume"; }

  resumeCommand(sessionId) {
    return `codex-aify --resume ${sessionId}`;
  }

  controllerFor(opts) {
    return new CodexController(opts);
  }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_CODEX_APP_SERVER_URL = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim() || "(unset)";
    return env;
  }

  // NO `discoverSessionId` OVERRIDE, deliberately: the base answers null. A codex agent's thread is the
  // one its launcher named (CODEX_THREAD_ID / AIFY_SESSION_HANDLE, exported only on an explicit
  // --resume), or none. This used to return the newest rollout under ~/.codex/sessions when no
  // app-server URL was set: any workspace's, the operator's own Codex Desktop conversation included.
  // Neither recency nor a matching cwd says whose a thread is.
}
