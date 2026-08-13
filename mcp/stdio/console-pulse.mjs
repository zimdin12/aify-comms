// Whether a console frame means the agent is GENERATING — and what to do about it.
//
// The dashboard console shows a live terminal. This decides, from what that terminal currently looks like,
// whether to pulse the agent's turn state. It is per-runtime because the evidence is per-runtime: claude
// prints a spinner footer while generating, and the other runtimes do not, so the same frame text means
// different things depending on who wrote it.
//
// IT DECIDES, IT DOES NOT ACT. The caller performs whatever the returned `kind` asks for. That is what makes
// this testable at all — it was previously reachable only by importing `server.js`, the bin entry point, so
// a test of twenty-three lines of decision logic loaded the whole bridge.
//
// A PULSE IS A CLAIM THAT WORK IS HAPPENING, so the bias is against inventing one: no agent id, or no
// specific signal, answers `{ kind: "none" }` rather than guessing. Over-pulsing would hold an idle agent at
// `working` — the exact latch this project has fixed repeatedly — while under-pulsing costs at most a slower
// status update, which the ordinary heartbeat then corrects.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

// Pure gate (exported for tests): given a terminal's runtime + console classification,
// decide which working pulse to emit. Claude uses the spinner-gated console-working
// lease (the strong, specific "claude is generating" signal — the TUI footer). Other
// runtimes keep the legacy any-output terminal pulse (they own native turn detectors).
export function decideConsolePulse({ runtime, consoleClass, agentId, turnInFlight = false }) {
  const aid = String(agentId || "").trim();
  if (!aid) return { kind: "none" };
  if (runtime === "claude-code") {
    // The spinner footer ("working") is the strong, specific generating signal → refresh.
    if (consoleClass === "working") return { kind: "console-working", agentId: aid };
    // Defense-in-depth (#224, 2026-06-18): a transient "unknown" footer frame mid-generation
    // (neither a clear spinner nor the idle prompt) must NOT let the lease lapse WHEN a turn is
    // already known in flight — refresh across the ambiguous frame. NEVER on "idle" (a clear
    // at-rest reading) and never when no turn is known, so this can't manufacture working at rest.
    if (consoleClass === "unknown" && turnInFlight) return { kind: "console-working", agentId: aid };
    return { kind: "none" };
  }
  // Non-claude runtimes (codex/hermes/pi) own native turn detectors (codex turn/completed,
  // hermes gateway idle/running, pi agent_end). The legacy any-output terminal pulse was
  // effectively DEAD before this change (stateFor omitted agentId, so it never fired), so we
  // keep it disabled rather than newly activating an untested output-based `working` for them.
  return { kind: "none" };
}
