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

export const CONSOLE_PROMPT_RULES = [
  {
    // Resume prompt. Operator policy: choose "Resume full session". The menu highlights
    // "Resume from summary" by default, so move down once and confirm.
    name: "resume-full-session",
    match: /Resume full session/i,
    mustAlsoMatch: /Resume from summary/i,
    answer: "\x1b[B\r",
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

// A claude interactive selection menu renders its highlighted option with a cursor
// glyph (❯ / › / ▶). PROSE that merely mentions "Resume full session" or "bypass
// permissions" has no such cursor — requiring one is the primary guard against the
// bridge typing keystrokes into a live turn when claude is just WRITING about a prompt.
const MENU_CURSOR_RE = /[❯›▶]/;

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
    return rule;
  }
  return null;
}
