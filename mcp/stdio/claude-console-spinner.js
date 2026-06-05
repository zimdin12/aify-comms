// Pure matching rules for the managed-claude TUI console tail. The spinner footer
// is a STRONG "claude is working" signal (the one thing that tracks LIVE generation,
// which the per-completed-message transcript cannot see). WEAK-by-default contract:
// only POSITIVE matches classify; anything unrecognized is "unknown" and never flips
// status — so this only ADDS `working` during the spinner window and never fights the
// authoritative transcript/Stop clear.

// CSI (\x1b[ ... letter), OSC (\x1b] ... BEL/ST), and 2-byte ESC sequences.
const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

export function stripAnsi(s = "") {
  return String(s || "").replace(ANSI_RE, "");
}

// Spinner footer: a spinner glyph + a verb + "for <N><unit>". Verbs are claude's
// rotating gerunds/past-tense ("Crunched", "Baked", "Wibbling", ...), so we match
// "<glyph> <word> for <number><h|m|s>" rather than enumerating verbs.
const SPINNER_RE = /[✱✶✽✺✹✷✵✳✢✻*·]\s+\S+\s+for\s+\d+\s*(?:h|m|s)\b/i;
// The interrupt hint rides with every in-progress claude turn.
const INTERRUPT_RE = /esc to interrupt/i;
// The idle prompt renders the shortcuts hint and no interrupt hint.
const IDLE_HINT_RE = /\?\s*for shortcuts/i;

// Index of the LAST match of `re` in `text`, or -1. The console tail ACCUMULATES
// (the old spinner line lingers above a freshly-rendered idle prompt), so "contains"
// is wrong — whichever signal appears LATEST in the byte stream is the live footer.
function lastIndexOfMatch(text, re) {
  const g = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
  let m;
  let idx = -1;
  while ((m = g.exec(text)) !== null) {
    idx = m.index;
    if (m.index === g.lastIndex) g.lastIndex++; // guard against a zero-width match loop
  }
  return idx;
}

// Classify the visible console tail. Returns "working" | "idle" | "unknown".
// The LATEST signal wins: a working footer (spinner / "esc to interrupt") rendered
// below an old idle prompt → working, and vice-versa. A tie of "neither" → unknown,
// so this never flips state on unrecognized output.
export function classifyClaudeConsoleTail(rawTail = "") {
  const visible = stripAnsi(rawTail).slice(-4000);
  const workingIdx = Math.max(
    lastIndexOfMatch(visible, INTERRUPT_RE),
    lastIndexOfMatch(visible, SPINNER_RE),
  );
  const idleIdx = lastIndexOfMatch(visible, IDLE_HINT_RE);
  if (workingIdx < 0 && idleIdx < 0) return "unknown";
  return workingIdx >= idleIdx ? "working" : "idle";
}
