#!/usr/bin/env node
// NOTE: the fixture frames here are REPRESENTATIVE of the claude TUI (spinner footer
// vs idle prompt). When the claude TUI version changes, re-capture a real console tail
// into these fixtures and re-tune SPINNER_RE / IDLE_HINT_RE if needed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { stripAnsi, classifyClaudeConsoleTail } from "../claude-console-spinner.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = (n) => readFileSync(join(here, "fixtures/claude-console", n), "utf8");

// stripAnsi removes CSI/OSC sequences but keeps visible text.
assert.equal(stripAnsi("\x1b[31m✻ Baked for 3m 55s\x1b[0m"), "✻ Baked for 3m 55s");

// Representative captured frames classify correctly.
assert.equal(classifyClaudeConsoleTail(fx("working-spinner.txt")), "working");
assert.equal(classifyClaudeConsoleTail(fx("idle-prompt.txt")), "idle");

// Synthetic invariants.
assert.equal(classifyClaudeConsoleTail("✻ Crunched for 14m 58s (esc to interrupt)"), "working");
assert.equal(classifyClaudeConsoleTail("✶ Wibbling for 5s"), "working");
// M-C (2026-06-05): the interrupt hint counts as working ONLY on a real footer line (a
// spinner glyph present). The other footer shape — "(<time> · esc to interrupt)" with no
// "for" — still classifies working because the glyph is on the line.
assert.equal(classifyClaudeConsoleTail("✻ Crunching… (12s · esc to interrupt)"), "working");
// ...but a BARE phrase, or claude's own PROSE mentioning it (no spinner glyph on that line),
// must NOT manufacture a working classification — the false-positive M-C closes.
assert.equal(classifyClaudeConsoleTail("esc to interrupt"), "unknown");
assert.equal(classifyClaudeConsoleTail("You can press esc to interrupt the running command."), "unknown");
assert.equal(classifyClaudeConsoleTail("│ > │\n  ? for shortcuts"), "idle");
// A stale 'esc to interrupt' far up in scrollback must NOT pin working when the live
// footer is the idle prompt.
assert.equal(
  classifyClaudeConsoleTail("esc to interrupt\n" + "x\n".repeat(2000) + "│ > │\n  ? for shortcuts"),
  "idle",
);
// Unrecognized text never flips state.
assert.equal(classifyClaudeConsoleTail("just some build log output\n"), "unknown");
assert.equal(classifyClaudeConsoleTail(""), "unknown");

console.log("claude-console-spinner.test.js: all assertions passed");

// ── Subagents / agents-manager working signal (2026-06-11) ───────────────────
import { hasActiveSubagents } from "../claude-console-spinner.js";
{
  const MANAGER_RUNNING = [
    "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents · ↓ to manage",
    "● main ↑/↓ to select · Enter to view",
    "◯ general-purpose Full integration re-validation pass 4m 51s · ↓ 48.7k tokens",
  ].join("\n");
  assert.equal(hasActiveSubagents(MANAGER_RUNNING), true, "manager + running row = subagents active");
  assert.equal(classifyClaudeConsoleTail(MANAGER_RUNNING), "working", "manager + running row classifies working (footer occluded)");
  // Manager chrome with only COMPLETED rows (tool uses, no elapsed) = not active.
  const MANAGER_IDLE = [
    "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents · ↓ to manage",
    "◯ general-purpose Done thing +73 tool uses · ↓ 53.2k tokens",
    "? for shortcuts",
  ].join("\n");
  assert.equal(hasActiveSubagents(MANAGER_IDLE), false, "manager with only completed rows is not subagents-active");
  assert.equal(classifyClaudeConsoleTail(MANAGER_IDLE), "idle", "idle prompt wins when no running row");
  // Prose mentioning tokens without manager chrome = nothing.
  assert.equal(hasActiveSubagents("we used 4m 2s · ↓ 9k tokens yesterday"), false, "no manager chrome → not subagents");
}
console.log("subagents detection assertions passed");
