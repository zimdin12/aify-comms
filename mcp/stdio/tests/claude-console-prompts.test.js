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
// move re-renders before the confirm; default is compact, one row down is full session).
const resume = matchConsolePrompt(fx("resume-prompt.txt"));
assert.equal(resume?.name, "resume-full-session");
assert.deepEqual(resume?.answer, ["\x1b[B", "\r"]);

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

// Every rule has the required shape: an answer is a string OR an array of keystroke strings.
for (const r of CONSOLE_PROMPT_RULES) {
  const okAnswer = typeof r.answer === "string"
    || (Array.isArray(r.answer) && r.answer.every((k) => typeof k === "string"));
  assert.ok(r.name && r.match instanceof RegExp && okAnswer);
}

console.log("claude-console-prompts.test.js: all assertions passed");
