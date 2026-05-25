// ClaudeController - extracted from createClaudeController in runtimes.js
// as part of Plan 3 Task 9.
//
// Claude Code's heavy delivery work lives in claude-channel.js (a sidecar
// inside claude-aify). This controller is intentionally thin: the bridge's
// main dispatch loop now drops 'managed' from supportedExecutionModes for
// claude-code (managed-via-wrapper preferred), and resident/channel mode
// claude delivery is handled by the channel bridge, not by launchRuntimeRun.
//
// Therefore launchRuntimeRun's claude-code branch is a "safety belt": if
// some code path still routes a claude-code run here, start() rejects with
// a clear error pointing operators at the right surface (claude-aify +
// resident channel bridge).
//
// File budget per 500-line rule: <=400 lines.

import { BaseController } from "./base-controller.js";
import { controlCapabilitiesForRuntime } from "../runtimes-helpers.js";

const CLAUDE_DISPATCH_DISABLED_MESSAGE =
  "Claude Code managed Messenger no longer uses claude -p. " +
  "Start or attach a Claude PTY/channel runtime with claude-aify, then deliver Messenger work through the resident channel bridge.";

export class ClaudeController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._capabilities = null;
  }

  // Returns the legacy controller shape ({ capabilities, interrupt, steer,
  // promise }) that launchRuntimeRun hands back to the runtime dispatcher.
  // start() always rejects: see file header for why.
  start() {
    if (!this._started) {
      this._started = true;
      this._capabilities = controlCapabilitiesForRuntime("claude-code");
    }
    return {
      capabilities: this._capabilities,
      interrupt: () => {},
      steer: async () => {
        throw new Error(CLAUDE_DISPATCH_DISABLED_MESSAGE);
      },
      promise: Promise.reject(new Error(CLAUDE_DISPATCH_DISABLED_MESSAGE)),
    };
  }

  async injectMessage(_opts) {
    // claude managed-via-wrapper delivers via the wrapper PTY's child bridge;
    // resident/channel delivery goes through claude-channel.js sidecar.
    throw new Error(CLAUDE_DISPATCH_DISABLED_MESSAGE);
  }

  async interrupt(_opts) {
    // No active turn owned by this controller - interrupts are routed
    // through the channel bridge or wrapper-PTY child bridge.
  }

  async steer(_opts) {
    throw new Error(CLAUDE_DISPATCH_DISABLED_MESSAGE);
  }
}
