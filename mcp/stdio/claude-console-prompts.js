// Centralized console matching-rules layer for the managed-claude TUI. Each rule maps
// a recognizable prompt (ANSI-stripped) to the keystrokes that answer it. The host types
// the answer once per on-screen appearance. Replaces scattered ad-hoc prompt handling so
// a freshly-spawned/restarted managed claude boots to a usable turn unattended.
//
// Keystrokes: "\r" Enter, "\x1b[B" Down, "\x1b[A" Up.
//
// Rule contract: { name, match: RegExp, answer: string, mustAlsoMatch?: RegExp }.
// A rule fires only when `match` (and `mustAlsoMatch`, if present) hit the live tail
// region. Rules are tried in order; the FIRST match wins. These patterns are claude-TUI
// VERSION-DEPENDENT — when claude changes its prompts, re-capture a frame into
// mcp/stdio/tests/fixtures/claude-console/ and re-tune the rule here (one place).
import { stripAnsi } from "./claude-console-spinner.js";

const DOWN = "\x1b[B";
const UP = "\x1b[A";
const ENTER = "\r";
const RESUME_FULL_RE = /Resume full session/i;
// A claude interactive selection menu renders its highlighted option with a cursor glyph
// (❯ / › / ▶). PROSE that merely mentions a prompt phrase has no such cursor — requiring one
// is the primary guard against typing keystrokes into a live turn. Declared up here (before
// computeResumeAnswer, which reads it at call-time) to avoid the hoisting smell.
const MENU_CURSOR_RE = /[❯›▶]/;

// CURSOR-AWARE resume selection (2026-06-05): operator policy is "Resume full session as-is".
// The earlier fix blindly sent [Down, Enter] assuming the menu always renders summary on the
// cursor row and full-session exactly one row below. That is a POSITIONAL bet on a
// version-dependent menu: if claude reorders/renumbers the options (or pre-selects full
// session), blind Down+Enter silently selects the WRONG entry. Instead we read where the
// cursor (❯) actually is and where the "Resume full session" line is, and move EXACTLY that
// many rows before Enter. If we cannot locate both (or they're implausibly far apart), we
// return null — the rule does NOT fire and we let the operator / settle handle it rather than
// guess-press. The keystrokes stay a SPACED sequence (the host's _sendAnswer delays between
// them) so the Ink/React menu move re-renders before the Enter confirm.
function computeResumeAnswer(visible) {
  const lines = visible.split(/\r?\n/);
  // The live, focused menu is the LAST cursor glyph in the tail.
  let cursorIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (MENU_CURSOR_RE.test(lines[i])) { cursorIdx = i; break; }
  }
  if (cursorIdx < 0) return null;
  // The "Resume full session" option line CLOSEST to the cursor line (the live menu, not
  // a prose mention scrolled above it).
  let targetIdx = -1;
  let best = Infinity;
  for (let i = 0; i < lines.length; i++) {
    if (!RESUME_FULL_RE.test(lines[i])) continue;
    const d = Math.abs(i - cursorIdx);
    if (d < best) { best = d; targetIdx = i; }
  }
  if (targetIdx < 0) return null;
  const delta = targetIdx - cursorIdx;
  if (Math.abs(delta) > 9) return null; // implausible spread → don't guess-press
  const keys = [];
  for (let i = 0; i < Math.abs(delta); i++) keys.push(delta > 0 ? DOWN : UP);
  keys.push(ENTER);
  return keys;
}

export const CONSOLE_PROMPT_RULES = [
  {
    // Resume prompt → select "Resume full session as-is" by CURSOR POSITION (see
    // computeResumeAnswer). computeAnswer returns the exact move+Enter sequence, or null to
    // abort if the menu can't be read (so we never select the wrong resume entry blindly).
    name: "resume-full-session",
    match: /Resume full session/i,
    mustAlsoMatch: /Resume from summary/i,
    computeAnswer: computeResumeAnswer,
  },
  {
    // Compaction question on resume. Tuned against
    // fixtures/claude-console/compaction-prompt.txt; Enter accepts the highlighted option.
    name: "compaction-question",
    match: /continue without compact|compact[\s\S]{0,80}continue/i,
    answer: "\r",
  },
  {
    // Bypass-permissions accept dialog. Tuned against
    // fixtures/claude-console/perms-accept.txt; Enter confirms the highlighted accept.
    name: "bypass-permissions-accept",
    match: /bypass permissions[\s\S]{0,160}(accept|yes, i accept|continue)/i,
    answer: "\r",
  },
  {
    // Channel auto-enter: accept the development-channels prompt so a dispatched channel
    // wake lands instead of stranding at the prompt. Tuned against
    // fixtures/claude-console/channel-enter.txt.
    name: "channel-enter",
    match: /development-channels|enter channel|join channel/i,
    answer: "\r",
  },
];

// Match the live tail region against the rules. Returns the first matching rule or null.
// Only the last ~2KB of visible text is considered so a scrolled-away prompt is ignored.
// SAFETY: an interactive menu cursor (❯) MUST be present — otherwise the "match" is
// claude's own prose, not a focused prompt awaiting input, and answering it would inject
// stray keystrokes mid-turn. (The caller additionally gates on consoleClass !== "working".)
export function matchConsolePrompt(rawTail = "") {
  const visible = stripAnsi(rawTail).slice(-2000);
  if (!MENU_CURSOR_RE.test(visible)) return null;
  for (const rule of CONSOLE_PROMPT_RULES) {
    if (!rule.match.test(visible)) continue;
    if (rule.mustAlsoMatch && !rule.mustAlsoMatch.test(visible)) continue;
    if (rule.computeAnswer) {
      // Rule matched, but the keystrokes are computed from the live frame. A null result
      // means "matched but cannot answer safely" → do NOT fire (don't fall through to a
      // different rule either; the focused prompt is this one).
      const answer = rule.computeAnswer(visible);
      if (answer == null) return null;
      return { ...rule, answer };
    }
    return rule;
  }
  return null;
}
