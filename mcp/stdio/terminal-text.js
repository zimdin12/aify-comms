// Pure text handling for terminal output: strip the noise, decide what a line MEANS, and take a
// resume out of a command or an env.
//
// Extracted from `terminal-runtime.js` in v0.5.4. That file is a 627-line `TerminalProcessManager`
// with a hundred lines of pure functions stacked above it, and the two halves are tested
// differently: these can be driven by calling them, the manager needs a spawned process. Splitting
// on that line is the same seam the dashboard chat modules were split on.
//
// `classifyTerminalRuntimeOutput` IS THE ONE THAT MATTERS. A terminal that dies seconds after
// attaching says why in its own output, and this is what turns that text into a verdict the bridge
// can act on — an auth failure and an unresumable session handle need different recoveries, and
// reporting either as a generic death loses the difference. It returns null rather than guessing
// when the text says nothing it recognises.
//
// THE RESUME STRIPPERS ARE A PAIR AND MUST STAY ONE. A retry after a failed resume has to drop the
// handle from BOTH the command line and the environment; leaving it in either is a retry that
// resumes the same dead session again.
//
// Bodies byte-identical to what stood in `terminal-runtime.js`; five gained `export` because their
// caller now imports them, which is the only substitution.
import { normalizeRuntime, runtimeCommandWithoutResume, sessionEnvVarsForRuntime } from "./runtimes.js";


// EVICTION (2026-07-14): the tail was 8192 bytes, and claude repaints a spinner + an OSC window
// title continuously while ANY background work is alive. A dialog the agent is STUCK on is
// therefore pushed out of the tail within seconds — measured on a real stuck console: 15.9KB of
// pure repaint noise had accumulated after the compaction dialog, so by the time anything looked,
// the prompt was gone. Two changes, both needed: drop the OSC title sequences (they carry no
// screen text, only noise), and keep a window big enough to survive the flood. A fixed regex
// alone would not have helped — it would have been matching a buffer the dialog had already left.
export const OSC_NOISE_RE = /\x1b\][^\x07]*(?:\x07|\x1b\\)/g;

export function appendTail(current = "", chunk = "", limit = 65536) {
  const next = `${current || ""}${String(chunk || "").replace(OSC_NOISE_RE, "")}`;
  return next.length > limit ? next.slice(-limit) : next;
}

export function compactTerminalText(text = "") {
  return String(text || "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, " ")
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function hermesResumeStillPending(text = "") {
  const lower = compactTerminalText(text).toLowerCase();
  if (!lower) return false;
  const resumeIdx = lower.lastIndexOf("resuming");
  if (resumeIdx < 0) return false;
  const readyIdx = lower.lastIndexOf("ready");
  return readyIdx < resumeIdx;
}

export function hermesResumeStallHealMs() {
  const raw = Number(process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS || "");
  if (Number.isFinite(raw) && raw > 0) return Math.max(25, raw);
  return 30000;
}

export function classifyTerminalRuntimeOutput(runtime = "", text = "") {
  const key = normalizeRuntime(runtime);
  const raw = String(text || "");
  const compact = compactTerminalText(raw);
  const lower = compact.toLowerCase();
  if (!lower) return null;
  if (key === "pi") {
    if (
      /no api key/.test(lower) ||
      /api key (?:not found|missing|required)/.test(lower) ||
      /not authenticated|authentication (?:failed|required)|unauthori[sz]ed|\b401\b/.test(lower) ||
      /amazon-bedrock|bedrock/.test(lower) && /login|auth|credential|api key/.test(lower)
    ) {
      return {
        kind: "auth",
        status: "failed",
        message: `Pi authentication failed fast: ${compact || "missing or expired provider credentials"}`,
      };
    }
    const missingSession = compact.match(/session\s+["']?([^"'\s]+)["']?\s+(?:not found|does not exist|missing)/i);
    if (missingSession || /session .*not found|session .*does not exist|no such session/i.test(compact)) {
      return {
        kind: "missing_session",
        status: "failed",
        sessionHandle: missingSession?.[1] || "",
        message: `Pi saved session handle is not resumable: ${compact}`,
      };
    }
  }
  if (key === "hermes") {
    const missingSession = compact.match(/session\s+["']?([^"'\s]+)["']?\s+(?:not found|does not exist|missing)/i);
    if (missingSession || /session .*not found|session .*does not exist|no such session/i.test(compact)) {
      return {
        kind: "missing_session",
        status: "failed",
        sessionHandle: missingSession?.[1] || "",
        message: `Hermes saved session handle is not resumable: ${compact}`,
      };
    }
  }
  return null;
}

export function terminalCommandWithoutResume(runtime = "", command = "") {
  return runtimeCommandWithoutResume(runtime, command);
}

export function terminalEnvWithoutResume(runtime = "", env = {}) {
  const next = { ...(env || {}) };
  delete next.AIFY_SESSION_HANDLE;
  for (const name of sessionEnvVarsForRuntime(runtime)) {
    delete next[name];
  }
  return next;
}
