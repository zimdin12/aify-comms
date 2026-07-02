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
  // The LIVE cursor row must itself be a resume-menu option — if the last cursor on
  // screen belongs to some OTHER dialog (the menu is stale scrollback), arrows computed
  // against it would land in that dialog instead (2026-06-12).
  if (!/Resume (?:from summary|full session)/i.test(lines[cursorIdx])) return null;
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
    // TIGHTENED (2026-06-10, same class as the bypass rule): the old second alternative
    // `compact…continue` matched PROSE ("compact the list and continue") — keep only the
    // dialog-literal phrasing.
    name: "compaction-question",
    match: /continue without compact/i,
    answer: "\r",
  },
  {
    // Bypass-permissions accept dialog. Tuned against
    // fixtures/claude-console/perms-accept.txt; Enter confirms the highlighted accept.
    // TIGHTENED (2026-06-10): the old /bypass permissions...(accept|continue)/ also matched
    // claude's ALWAYS-PRESENT footer chrome "bypass permissions on (shift+tab to cycle)" plus
    // any incidental "continue/accept" within 160 chars (e.g. a SUBAGENT's task title in the
    // background-agents manager, whose ❯ row cursor satisfied the menu gate while the manager
    // occluded the spinner) — the bridge then typed Enter into a live console, which the agents
    // manager interpreted as "Enter to view" (the operator-reported random agent-selection
    // screen). Require the real dialog shape: the "Bypass Permissions mode" warning + the
    // literal "Yes, I accept" option.
    name: "bypass-permissions-accept",
    match: /bypass permissions mode[\s\S]{0,200}yes, i accept/i,
    answer: "\r",
  },
  {
    // Channel auto-enter: accept the development-channels prompt so a dispatched channel
    // wake lands instead of stranding at the prompt. Tuned against
    // fixtures/claude-console/channel-enter.txt.
    // TIGHTENED again (2026-06-12, the auto-compact-on-resume incident): the previous
    // `development-channels` alternative matched ORDINARY BOOT OUTPUT — the worker's own
    // command line / plugin-load log line ("--dangerously-load-development-channels
    // server:aify-comms-channel") sits in the tail at exactly the moment the RESUME menu
    // renders, so this rule typed a blind Enter into the menu and selected the highlighted
    // "Resume from summary (recommended)" — silently summarizing (≈compacting) the session
    // on EVERY worker cold-start. Only the dialog's own question line identifies the real
    // prompt.
    name: "channel-enter",
    match: /enter channel to receive/i,
    answer: "\r",
  },
  {
    // Dev-channels ACKNOWLEDGMENT (2026-07-03, mc-vulkan-manager "up-but-deaf" incident):
    // when the wrapper launches claude with `--dangerously-load-development-channels
    // server:aify-comms-channel`, claude shows a first-run confirmation menu
    //   ❯ 1. I am using this for local development
    //     2. Exit
    //   Enter to confirm · Esc to cancel
    // The cursor defaults to the accept option, so a blind Enter confirms it. Without this
    // rule the worker booted, sat at this menu forever, never started its in-process MCP,
    // and never claimed dispatched runs (registered "online" but deaf). Distinct from
    // `channel-enter` (that is the LATER "enter channel to receive" prompt). Matched on the
    // acknowledgment's own question line so ordinary boot log lines mentioning
    // `--dangerously-load-development-channels` can't trip it; the cursor gate + resume-menu
    // interlock (blind-Enter rule) keep it from firing into a resume menu.
    name: "dev-channels-accept",
    match: /I am using this for local development/i,
    answer: "\r",
  },
];

// RESUME-MENU INTERLOCK (2026-06-12): while a resume menu is on screen — even PARTIALLY
// rendered (one option line painted, the other not yet) — no blind-Enter rule may fire.
// The menu's highlighted default is "Resume from summary (recommended)", so any stray
// Enter destroys the session's full context. Only the cursor-aware resume rule (which
// requires BOTH option lines and computes the exact moves) is allowed to answer it.
// ORDER-AWARE: the console tail ACCUMULATES, so menu text lingers in scrollback after
// the menu is answered — a blind rule is suppressed only when the resume-menu text is
// LATER in the byte stream than that rule's own dialog text (i.e. the menu is the live,
// focused thing). A channels/perms dialog rendered AFTER the menu still auto-answers.
const RESUME_MENU_ANY_RE = /Resume (?:from summary|full session)/i;

// Index of the LAST match of `re` in `text`, or -1 (latest-wins, same as the spinner
// classifier's helper).
function lastIndexOf(text, re) {
  const g = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
  let m;
  let idx = -1;
  while ((m = g.exec(text)) !== null) {
    idx = m.index;
    if (m.index === g.lastIndex) g.lastIndex++;
  }
  return idx;
}

// Match the live tail region against the rules. Returns the first matching rule or null.
// Only the last ~2KB of visible text is considered so a scrolled-away prompt is ignored.
// SAFETY: an interactive menu cursor (❯) MUST be present — otherwise the "match" is
// claude's own prose, not a focused prompt awaiting input, and answering it would inject
// stray keystrokes mid-turn. (The caller additionally gates on consoleClass !== "working".)
// Background-agents manager chrome (Claude Code's subagent panel). Its rows carry a ❯-style
// selection cursor and its footer ("← for agents", "↑/↓ to select · Enter to view") sits where
// the spinner footer would be — so while it is on screen the cursor gate is satisfied and the
// working classification can read unknown. The manager means claude is ORCHESTRATING SUBAGENTS,
// never stuck at a boot prompt: suppress ALL auto-answers while its chrome is visible
// (2026-06-10, the "random agent-selection screen" incident).
const AGENTS_MANAGER_RE = /← for agents|↑\/↓ to select|↓ to manage/;

// COMPACTION-RECOMMENDATION dialog (2026-07-02, operator incident: managed agents stalled
// at "/compact → ❯ 1. Resume from summary (recommended) — Enter to confirm" while managers
// waited). This is a DIFFERENT dialog from the cold-start resume menu: it renders during a
// compaction flow, often with ONLY the summary option visible, plus the usage-limits
// sentence — so the two-option cursor-aware resume rule can never fire on it. Confirming
// the highlighted recommended option is the intended outcome here (the operator asked for
// compaction to proceed unattended), unlike the cold-start menu where we deliberately
// preserve the full session. Fires ONLY when the live cursor row IS the summary option and
// the usage-limits/compacting sentence is present; disable via
// matchConsolePrompt(tail, { autoConfirmCompaction: false }) (host env override:
// AIFY_AUTO_CONFIRM_COMPACTION=0) or the `console_auto_confirm_claude_compaction` setting.
const COMPACTION_FLOW_RE = /(substantial portion of your usage limits|Compacting conversation|We recommend resuming from a summary)/i;

function computeCompactionConfirmAnswer(visible) {
  const lines = visible.split(/\r?\n/);
  let cursorIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (MENU_CURSOR_RE.test(lines[i])) { cursorIdx = i; break; }
  }
  if (cursorIdx < 0) return null;
  // Only confirm when the LIVE cursor row is the recommended summary option itself.
  if (!/Resume from summary/i.test(lines[cursorIdx])) return null;
  return [ENTER];
}

export function matchConsolePrompt(rawTail = "", opts = {}) {
  const visible = stripAnsi(rawTail).slice(-2000);
  if (!MENU_CURSOR_RE.test(visible)) return null;
  if (AGENTS_MANAGER_RE.test(visible)) return null;
  // Compaction-recommendation confirm — checked BEFORE the general loop because its own
  // option line ("Resume from summary") is exactly what the resume-menu interlock keys on;
  // the interlock exists to protect the COLD-START menu, not this flow. The cursor-row
  // requirement in computeCompactionConfirmAnswer keeps it from firing on prose or on the
  // two-option cold-start menu mid-navigation (there the usage sentence is absent anyway).
  if (opts.autoConfirmCompaction !== false && COMPACTION_FLOW_RE.test(visible)) {
    const compactIdx = lastIndexOf(visible, /Resume from summary/i);
    const fullIdx = lastIndexOf(visible, RESUME_FULL_RE);
    // Defer to the cursor-aware cold-start rule ONLY when a live TWO-OPTION menu is on
    // screen — its options render adjacent (within a couple of lines), so byte proximity
    // discriminates it from a stale "Resume full session" mention lingering in scrollback
    // above a live one-option compaction dialog.
    const twoOptionMenuLive = fullIdx >= 0 && compactIdx >= 0 && Math.abs(fullIdx - compactIdx) <= 200;
    if (!twoOptionMenuLive && compactIdx >= 0) {
      const answer = computeCompactionConfirmAnswer(visible);
      if (answer != null) {
        return { name: "compaction-resume-summary-confirm", match: COMPACTION_FLOW_RE, answer };
      }
      return null; // dialog present but cursor not on the option yet — wait, don't guess
    }
  }
  // RECENCY-FIRST (2026-06-12): the tail ACCUMULATES, so an already-answered dialog's
  // text lingers in scrollback while a NEW dialog renders below it. The live, focused
  // prompt is whichever dialog text appears LATEST in the byte stream — so among the
  // matching rules, the one with the highest last-match index wins (rule order is only
  // the tiebreak via >). Previously "first rule in array order wins" let a scrolled-away
  // resume menu re-claim a live channels dialog and compute arrows against the wrong
  // cursor.
  const resumeMenuIdx = lastIndexOf(visible, RESUME_MENU_ANY_RE);
  let bestRule = null;
  let bestIdx = -1;
  for (const rule of CONSOLE_PROMPT_RULES) {
    if (!rule.match.test(visible)) continue;
    if (rule.mustAlsoMatch && !rule.mustAlsoMatch.test(visible)) continue;
    const idx = lastIndexOf(visible, rule.match);
    if (!rule.computeAnswer && resumeMenuIdx > idx) {
      // The resume menu is LIVE-er than this rule's dialog text — a blind Enter here
      // would select "Resume from summary" and summarize away the session. Skip.
      continue;
    }
    if (idx > bestIdx) {
      bestIdx = idx;
      bestRule = rule;
    }
  }
  if (!bestRule) return null;
  if (bestRule.computeAnswer) {
    // Rule matched, but the keystrokes are computed from the live frame. A null result
    // means "matched but cannot answer safely" → do NOT fire (don't fall through to a
    // different rule either; the focused prompt is this one).
    const answer = bestRule.computeAnswer(visible);
    if (answer == null) return null;
    return { ...bestRule, answer };
  }
  return bestRule;
}
