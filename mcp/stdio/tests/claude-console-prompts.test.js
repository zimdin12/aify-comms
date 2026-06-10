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

console.log("claude-console-prompts.test.js: all assertions passed");
