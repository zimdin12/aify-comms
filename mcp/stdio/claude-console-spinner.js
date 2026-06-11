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
// (`*`/`·` are allowed here because the FULL "<glyph> <verb> for <N><unit>" shape is strong
// enough that a prose bullet can't spoof it — UNLIKE the bare INTERRUPT_RE below, which is why
// that one is intentionally restricted to true spinner glyphs. Don't "unify" the two classes.)
const SPINNER_RE = /[✱✶✽✺✹✷✵✳✢✻*·]\s+\S+\s+for\s+\d+\s*(?:h|m|s)\b/i;
// The interrupt hint rides with every in-progress claude turn — but count it as a working
// signal ONLY when a real spinner glyph is on the SAME LINE (the live footer). The bare
// phrase matched ANYWHERE let claude's own PROSE ("press esc to interrupt …") manufacture a
// false `working` classification (and the 12s→20s lease TTL widened that window). Requiring
// a spinner glyph on the footer line keeps BOTH real footer shapes — "✻ Verb for 12s (esc to
// interrupt)" and "✻ Verb… (12s · esc to interrupt)" — while rejecting a prose mention. (Only
// true spinner glyphs are required here, not the `*`/`·` that also appear as prose bullets.)
const INTERRUPT_RE = /[✱✶✽✺✹✷✵✳✢✻][^\n]*esc to interrupt/i;
// The idle prompt renders the shortcuts hint and no interrupt hint.
const IDLE_HINT_RE = /\?\s*for shortcuts/i;

// Background-agents manager (2026-06-11): when claude runs SUBAGENTS the manager panel
// OCCLUDES the spinner footer, so the lease lapsed and a hard-working claude read `online`
// (the next-manager incident). The manager's chrome ("← for agents" / "↑/↓ to select" /
// "↓ to manage") plus a RUNNING agent row (elapsed time + live token counter, e.g.
// "4m 51s · ↓ 48.7k tokens") is a strong positive working signal. Chrome alone is NOT
// enough — the manager can stay on screen while everything is idle (completed rows show
// "+N tool uses · ↓ Nk tokens" with no elapsed time, and don't match the running row).
const AGENTS_MANAGER_CHROME_RE = /← for agents|↑\/↓ to select|↓ to manage/;
const AGENTS_RUNNING_ROW_RE = /\b(?:\d+h\s+)?(?:\d+m\s+)?\d+s\s*·\s*↓\s*[\d.]+k tokens/;

// True when the agents manager is visible WITH at least one running row — claude is
// orchestrating subagents right now.
export function hasActiveSubagents(rawTail = "") {
  const visible = stripAnsi(rawTail).slice(-4000);
  return AGENTS_MANAGER_CHROME_RE.test(visible) && AGENTS_RUNNING_ROW_RE.test(visible);
}

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
  // The agents manager occludes the spinner footer; a visible manager with a RUNNING row
  // means claude is working (orchestrating subagents) regardless of footer position.
  if (AGENTS_MANAGER_CHROME_RE.test(visible) && AGENTS_RUNNING_ROW_RE.test(visible)) {
    return "working";
  }
  const workingIdx = Math.max(
    lastIndexOfMatch(visible, INTERRUPT_RE),
    lastIndexOfMatch(visible, SPINNER_RE),
  );
  const idleIdx = lastIndexOfMatch(visible, IDLE_HINT_RE);
  if (workingIdx < 0 && idleIdx < 0) return "unknown";
  return workingIdx >= idleIdx ? "working" : "idle";
}
