#!/usr/bin/env node
// NOTE: the prompt fixtures here are REPRESENTATIVE of the claude TUI. When the claude
// TUI version changes, re-capture the real frames into fixtures/claude-console/ and
// re-tune the rule regexes in claude-console-prompts.js (one place).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { matchConsolePrompt, CONSOLE_PROMPT_RULES } from "../claude-console-prompts.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = (n) => readFileSync(join(here, "fixtures/claude-console", n), "utf8");

// Resume prompt -> Resume full session = down THEN enter (a spaced sequence so the menu
// move re-renders before the confirm; in the default layout the cursor is on summary and
// full session is one row down).
const resume = matchConsolePrompt(fx("resume-prompt.txt"));
assert.equal(resume?.name, "resume-full-session");
assert.deepEqual(resume?.answer, ["\x1b[B", "\r"]);

// CURSOR-AWARE (2026-06-05): the answer is computed from the cursor's real position, not a
// blind down. If claude reorders so full-session is ALREADY the cursor row, we press Enter
// only (the old blind down+enter would have wrongly moved to summary).
const resumeReordered = matchConsolePrompt(
  "Resume session?\n\n❯ 1. Resume full session as-is\n  2. Resume from summary (recommended)\n",
);
assert.equal(resumeReordered?.name, "resume-full-session");
assert.deepEqual(resumeReordered?.answer, ["\r"], "cursor already on full session → Enter only");

// If full-session is ABOVE the cursor, move UP the exact number of rows then Enter.
const resumeFullAbove = matchConsolePrompt(
  "Resume session?\n\n  1. Resume full session as-is\n❯ 2. Resume from summary (recommended)\n",
);
assert.deepEqual(resumeFullAbove?.answer, ["\x1b[A", "\r"], "full session one row above cursor → Up+Enter");

// Compaction question + perms accept + channel enter all match (Enter to confirm).
assert.equal(matchConsolePrompt(fx("compaction-prompt.txt"))?.name, "compaction-question");
assert.equal(matchConsolePrompt(fx("perms-accept.txt"))?.name, "bypass-permissions-accept");
assert.equal(matchConsolePrompt(fx("channel-enter.txt"))?.name, "channel-enter");

// Dev-channels acknowledgment ("I am using this for local development") auto-confirms with
// Enter — without it the worker sat at the menu forever, never claimed runs (up-but-deaf,
// mc-vulkan-manager incident 2026-07-03). A bare boot-log mention of the flag does NOT fire.
{
  const devChannels = matchConsolePrompt(fx("dev-channels-accept.txt"));
  assert.equal(devChannels?.name, "dev-channels-accept");
  assert.deepEqual(devChannels?.answer, "\r");
  assert.equal(
    matchConsolePrompt(
      "  --dangerously-load-development-channels server:aify-comms-channel\n  Loading plugin...\n  ❯ > \n",
    ),
    null,
    "a boot-log line mentioning the flag (no acknowledgment question) must not fire",
  );
}

// An idle screen / spinner matches no prompt rule.
assert.equal(matchConsolePrompt("│ > │\n  ? for shortcuts"), null);
assert.equal(matchConsolePrompt("✻ Crunched for 3m 12s (esc to interrupt)"), null);
assert.equal(matchConsolePrompt(""), null);

// The resume rule needs BOTH options present (mustAlsoMatch) — a stray "Resume full
// session" mention without the menu does not fire.
assert.equal(matchConsolePrompt("note: you can Resume full session later"), null);

// B1 (keystroke-injection guard): claude's OWN prose that mentions the prompt phrases
// but has NO interactive menu cursor (❯) must NEVER match — otherwise a generating
// claude writing about these topics gets stray keystrokes typed into its PTY.
assert.equal(matchConsolePrompt("The menu offers Resume from summary and Resume full session as-is."), null);
assert.equal(matchConsolePrompt("I'll continue without compacting the context for now."), null);
assert.equal(matchConsolePrompt("Claude is running in bypass permissions mode; yes, I accept the risk."), null);
assert.equal(matchConsolePrompt("Loaded the development-channels server earlier this turn."), null);

// Only the live tail region matches: a resume menu far up in scrollback under a current
// idle prompt does NOT match (avoid answering a scrolled-away prompt).
assert.equal(
  matchConsolePrompt(fx("resume-prompt.txt") + "\n" + "x\n".repeat(2000) + "│ > │\n  ? for shortcuts"),
  null,
);

// Every rule has the required shape: a static `answer` (string OR array of keystroke strings)
// OR a `computeAnswer` function that derives the keystrokes from the live frame.
for (const r of CONSOLE_PROMPT_RULES) {
  const okAnswer = typeof r.answer === "string"
    || (Array.isArray(r.answer) && r.answer.every((k) => typeof k === "string"))
    || typeof r.computeAnswer === "function";
  assert.ok(r.name && r.match instanceof RegExp && okAnswer);
}

// ── Agents-manager incident regressions (2026-06-10) ────────────────────────
// The background-agents manager footer + a ❯ row cursor + a subagent task title containing
// "continue" must fire NOTHING — the old bypass rule matched the always-present footer chrome
// and typed Enter ("Enter to view") into a live console.
const AGENTS_MANAGER_FRAME = [
  "❯ main  4m 51s · ↓ 48.7k tokens",
  "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents · ↓ to manage",
  "● main ↑/↓ to select · Enter to view",
  "◯ general-purpose Full integration re-validation continue docs pass 4m 51s",
].join("\n");
assert.equal(matchConsolePrompt(AGENTS_MANAGER_FRAME), null,
  "agents-manager chrome must never fire an auto-answer");

// The REAL bypass dialog (fixture shape) still fires.
assert.equal(
  matchConsolePrompt("WARNING: Claude Code running in Bypass Permissions mode\nBy continuing you bypass permissions for all tool calls.\n❯ Yes, I accept\n  No, exit")?.name,
  "bypass-permissions-accept",
  "the real bypass dialog still auto-accepts",
);

// Prose with "compact … continue" / "join channel" near a cursor must NOT fire (loose-rule class).
assert.equal(matchConsolePrompt("❯ next: compact the list and continue with the merge"), null,
  "prose 'compact…continue' must not fire the compaction rule");
assert.equal(matchConsolePrompt("❯ then join channel #dev and report"), null,
  "prose 'join channel' must not fire the channel rule");

// The real compaction + channel dialogs still fire.
assert.equal(
  matchConsolePrompt("This conversation is large.\nContinue without compacting the context?\n❯ Yes, continue\n  No, compact first")?.name,
  "compaction-question",
);
assert.equal(
  matchConsolePrompt("Loading development-channels: server:aify-comms-channel\nEnter channel to receive dispatched messages?\n❯ Yes")?.name,
  "channel-enter",
);

// ── Auto-compact-on-resume incident regressions (2026-06-12) ─────────────────
// The worker's own boot output contains "--dangerously-load-development-channels
// server:aify-comms-channel" — the old channel rule matched that SUBSTRING and typed a
// blind Enter into the freshly-rendered resume menu, selecting the highlighted "Resume
// from summary (recommended)" → the session got summarized (≈compacted) on EVERY cold
// start (operator: "it should not auto compact each time").
const BOOT_ECHO = "claude --dangerously-load-development-channels server:aify-comms-channel --settings C:/tmp/x";

// (1) Boot echo + PARTIALLY rendered resume menu (only the highlighted summary row painted
// yet) → NOTHING fires. The old rule fired channel-enter here and compacted the session.
assert.equal(
  matchConsolePrompt(`${BOOT_ECHO}\nResume session?\n❯ 1. Resume from summary (recommended)`),
  null,
  "partial resume menu + boot echo must fire nothing (the auto-compact incident)",
);

// (2) Boot echo + FULL resume menu → the cursor-aware resume rule picks full session.
{
  const m = matchConsolePrompt(
    `${BOOT_ECHO}\nResume session?\n❯ 1. Resume from summary (recommended)\n  2. Resume full session as-is`,
  );
  assert.equal(m?.name, "resume-full-session");
  assert.deepEqual(m?.answer, ["\x1b[B", "\r"], "one Down + Enter selects full session");
}

// (3) Resume menu answered and scrolled ABOVE; the channels dialog renders BELOW (live)
// → channel-enter still fires (the interlock is order-aware, not a blanket suppress).
assert.equal(
  matchConsolePrompt(
    "❯ 1. Resume from summary (recommended)\n  2. Resume full session as-is\n…resumed…\n" +
    "Enter channel to receive dispatched messages?\n❯ Yes",
  )?.name,
  "channel-enter",
  "a channels dialog rendered after the menu still auto-answers",
);

// (4) The plugin-load log line alone (no dialog question) must never fire — even with an
// idle ❯ input box satisfying the cursor gate.
assert.equal(
  matchConsolePrompt("Loading development-channels: server:aify-comms-channel\n❯ "),
  null,
  "the boot log line is not the channels dialog",
);

// (5) A live resume menu rendered AFTER older perms-dialog text must suppress that blind
// Enter too (the interlock covers every blind rule, not just channel-enter).
assert.equal(
  matchConsolePrompt(
    "WARNING: Claude Code running in Bypass Permissions mode\nBy continuing you bypass permissions for all tool calls.\n❯ Yes, I accept\n" +
    "…\nResume session?\n❯ 1. Resume from summary (recommended)",
  ),
  null,
  "a live resume menu suppresses earlier blind-dialog text",
);

console.log("claude-console-prompts.test.js: all assertions passed");

// COMPACTION-RECOMMENDATION dialog (2026-07-02 operator incident): the /compact flow's
// one-option "Resume from summary (recommended)" dialog — with the usage-limits sentence
// and NO live "Resume full session" option — auto-confirms with Enter (managed agents were
// stalling here while managers waited on the compaction decision).
const COMPACT_DIALOG =
  "✻ Compacting conversation…\n" +
  "This session is 1h 58m old and 329.6k tokens.\n" +
  "Resuming the full session will consume a substantial portion of your usage limits. " +
  "We recommend resuming from a summary.\n\n" +
  "❯ 1. Resume from summary (recommended)\n" +
  "Enter to confirm · Esc to cancel\n";
const compactConfirm = matchConsolePrompt(COMPACT_DIALOG);
assert.equal(compactConfirm?.name, "compaction-resume-summary-confirm");
assert.deepEqual(compactConfirm?.answer, ["\r"], "confirm the highlighted recommended option");

// Opt-out: autoConfirmCompaction:false disables the rule (server setting / env off-switch).
assert.equal(matchConsolePrompt(COMPACT_DIALOG, { autoConfirmCompaction: false }), null);

// Cursor NOT on the summary row → wait, never guess-press.
assert.equal(
  matchConsolePrompt(
    "Resuming the full session will consume a substantial portion of your usage limits.\n" +
    "  1. Resume from summary (recommended)\n❯ 2. Something else entirely\n",
  ),
  null,
);

// A live TWO-OPTION cold-start menu (options adjacent) still routes to the cursor-aware
// full-session rule even when compaction prose is in the tail — full-context preservation
// governs two-option menus.
const twoOptionWithProse = matchConsolePrompt(
  "We recommend resuming from a summary.\n\n" +
  "❯ 1. Resume from summary (recommended)\n  2. Resume full session as-is\n",
);
assert.equal(twoOptionWithProse?.name, "resume-full-session");

// A STALE full-session mention far above (scrollback) must NOT block the live one-option
// compaction dialog from confirming.
const staleFullAbove = matchConsolePrompt(
  "earlier: chose Resume full session as-is\n" + "y\n".repeat(400) +
  "Resuming the full session will consume a substantial portion of your usage limits.\n" +
  "❯ 1. Resume from summary (recommended)\nEnter to confirm · Esc to cancel\n",
);
assert.equal(staleFullAbove?.name, "compaction-resume-summary-confirm");

// DATA-LOSS REGRESSION (bughunt 2026-07-03): a large-session cold-start `--resume`
// menu renders progressively — "Resume from summary" paints before "Resume full
// session". Its summary blurb ("We recommend resuming from a summary") matches the
// compaction phrase, but NOT the unambiguous in-session markers. Mid-render (summary
// painted, full not yet, cursor on summary) it must NOT auto-press Enter and compact
// the session away — the ambiguous phrase alone no longer auto-confirms.
const halfPaintedColdStart = matchConsolePrompt(
  "Resuming from your previous session.\n" +
  "We recommend resuming from a summary.\n\n" +
  "❯ 1. Resume from summary (recommended)\n" +
  "Enter to confirm · Esc to cancel\n",
);
assert.equal(
  halfPaintedColdStart?.name ?? null,
  null,
  "half-painted cold-start resume menu (ambiguous phrase, no unambiguous compaction marker) must NOT auto-confirm summary",
);

console.log("claude-console-prompts compaction-confirm tests passed");
