// ClaudeController - extracted from createClaudeController in runtimes.js
// as part of Plan 3 Task 9.
//
// Claude Code's heavy delivery work lives in claude-channel.js (a sidecar
// inside claude-aify). This controller is intentionally thin: ALL managed
// claude delivery is owned by that claude-channel.js CHANNEL-SIDECAR, not by
// a wrapper-PTY child (the wrapper-child is replies-only, and
// _managed_via_wrapper_for_runtime() returns False for claude-code). Both
// resident and managed claude work is delivered by the channel bridge, not
// by launchRuntimeRun.
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
      // Plan 4 ready: claude-aify is "ready" by virtue of being launched —
      // BOTH resident and managed claude delivery flow through the
      // claude-channel.js channel-sidecar (claude is NOT managed-via-wrapper).
      // Mark ready immediately so operators see the same status surface as
      // other runtimes. See DECISIONS.md.
      this.markReady();
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
    // All managed/resident claude delivery goes through the claude-channel.js
    // channel-sidecar — claude is NOT managed-via-wrapper, so nothing should
    // route an inject here.
    throw new Error(CLAUDE_DISPATCH_DISABLED_MESSAGE);
  }

  async interrupt(_opts) {
    // No active turn owned by this controller - interrupts are routed
    // through the claude-channel.js channel-sidecar.
  }

  async steer(_opts) {
    throw new Error(CLAUDE_DISPATCH_DISABLED_MESSAGE);
  }
}
