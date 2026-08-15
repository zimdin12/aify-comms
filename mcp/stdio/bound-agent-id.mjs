// Which agent is this bridge process bound to — read once, in one place.
//
// EXTRACTED IN v0.5.4 FROM THREE NEAR-COPIES that had already drifted. `claude-channel.js`,
// `hermes-channel.js` and `hermes-managed-host.js` each declared a `readBoundAgentId` doing the same
// PID-keyed file read, and two of the three fell back to `AIFY_AGENT_ID` while the third fell back
// to the empty string. Nothing recorded that difference as a decision; it is simply what three
// copies of a function look like after separate edits.
//
// So the SHARED part lives here and the FALLBACK stays at each call site, spelled out. A caller that
// wants the environment identity when no binding file exists passes it; a caller that wants "unbound
// means unbound" passes nothing. The divergence is now one visible argument instead of three bodies
// that have to be diffed to notice it.
//
// WHY THE FILE IS KEYED BY ppid AND NOT pid: `server.js` writes the binding on `comms_register`, and
// both it and the sidecar are children of the same Claude Code process — they share a parent, not a
// pid. Keying by the bridge's own pid would look up a file nobody wrote.

import { readAgentBindingFile } from "./binding-file.js";

/**
 * The agent id bound to this process, or `fallback` when no binding file exists yet.
 *
 * `fallback` is REQUIRED to be passed explicitly rather than defaulted to the environment, because
 * defaulting it is what made the three copies disagree without anyone choosing.
 */
export function boundAgentId({ dir, fallback = "" } = {}) {
  try {
    const binding = readAgentBindingFile({ pid: process.ppid || process.pid, dir });
    if (binding.agentId) return binding.agentId;
  } catch {
    // No binding file yet, or unreadable — the fallback is the caller's decision, not this one's.
  }
  return fallback;
}

/** The environment identity, trimmed. The fallback two of the three callers want. */
export function envAgentId() {
  return String(process.env.AIFY_AGENT_ID || "").trim();
}
