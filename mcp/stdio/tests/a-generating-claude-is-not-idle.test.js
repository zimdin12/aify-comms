#!/usr/bin/env node
// A claude that is generating must not read as `online`.
//
// WHAT THE OPERATOR SAW, with a screenshot, on 2026-08-26: a green dot beside sc-claude in the chat
// rail while its console showed `✻ Concocting… (7m 29s · ↓ 27.6k tokens)` — seven and a half minutes
// into a turn. Green is `online`; a working agent is drawn amber.
//
// THE CHAIN. `classifyClaudeConsoleTail` reads the console tail and answers working/idle/unknown.
// `decideConsolePulse` turns `working` into a `console-working` pulse, which takes a lease on the
// server that holds the agent at `working`. No `working` classification means no pulse, no lease, and
// the roster falls back to `online`.
//
// WHY IT CLASSIFIED NOTHING. Every existing working rule needs either "esc to interrupt" or the
// literal shape `<verb> for <N><unit>`, both on a line that also carries a spinner glyph. The modern
// footer has NONE of those: no interrupt hint, a gerund with an ellipsis instead of "for 21s", and —
// this is the part that defeats a glyph rule outright — claude repaints the spinner CELL by itself
// with an absolute cursor move between frames, so in the flattened tail the glyph and the footer text
// are never adjacent. Measured on the captured tail: after stripAnsi the footer reads
// `·Concocting… (8m 49s · ↓ 34.7k tokens)`, with a stray middle dot where the glyph should be.
//
// THE FIXTURE IS THAT AGENT'S OWN 20KB OF PTY OUTPUT, captured from the live service while the
// operator was reporting the bug, and written as BYTES. The first capture went through a cp1257
// console, lost all 129 spinner glyphs, and sent me looking for an encoding defect that did not
// exist — the raw HTTP response had 161 intact `E2 9C BB` sequences all along.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { classifyClaudeConsoleTail } from "../claude-console-spinner.js";
import { TerminalProcessManager } from "../terminal-runtime.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const TAIL = readFileSync(join(HERE, "fixtures", "claude-generating-console-tail.txt"), "utf8");

test("the fixture is a GENERATING console, not just any console", () => {
  // Anti-vacuity, and the first thing to check if this file ever goes red for the wrong reason.
  assert.match(TAIL, /Concocting…/, "the fixture no longer holds a live footer");
  assert.match(TAIL, /↓ [\d.]+k tokens/, "the fixture no longer holds a token counter");
  assert.ok(TAIL.length > 15000, `fixture is only ${TAIL.length} chars`);
});

test("a claude generating for minutes classifies as WORKING, not unknown", () => {
  assert.equal(
    classifyClaudeConsoleTail(TAIL), "working",
    "the console tail of an agent mid-generation was not recognised as working, so no pulse fires, "
      + "no lease is taken, and the roster shows the agent as online with a green dot",
  );
});

test("and the PRODUCTION path carries that classification, not just the classifier", async () => {
  // THE CALL SITE, because the classification is worthless if nothing acts on it. A pure test of the
  // classifier alone would have passed while the pulse still never fired.
  //
  // THE ACTOR CHANGED IN v0.6.2 AND THE PROPERTY DID NOT. This used to call `decideConsolePulse`,
  // which turned `working` into a console-working lease; that function was the environment bridge's
  // (`terminal-manager.mjs` was its only caller) and was deleted with it. `_handleOutput` is what
  // classifies a live console now, and aify-env owns the pulse. So the question is asked of the code
  // that answers it today: does THIS tail, fed the way a real PTY feeds it, come out working?
  const mgr = new TerminalProcessManager({ onOutput: async () => {} });
  const state = { id: "t-gen", runtime: "claude-code", agentId: "sc-claude", outputTail: "" };
  mgr.terminals.set("t-gen", state);
  await mgr._handleOutput("t-gen", state, TAIL);
  assert.equal(
    mgr.stateFor("t-gen").consoleClass, "working",
    "the operator's own captured console reached the runtime and came out un-classified, so nothing "
      + "downstream can tell a seven-minute generation from an idle prompt",
  );
});

test("the OLD footer shape still works, so nothing was traded away", () => {
  assert.equal(classifyClaudeConsoleTail("✻ Sauteing for 21s (esc to interrupt)"), "working");
});

test("the completed residue is still IDLE evidence", () => {
  // This distinction is load-bearing and predates the change: a bypass-permissions session renders no
  // "? for shortcuts", so the completed-thought line is the ONLY idle signal it emits. Counting it as
  // working pinned idle agents at `working` forever once before.
  assert.equal(classifyClaudeConsoleTail("✻ Sauteed for 21s"), "idle");
  assert.equal(classifyClaudeConsoleTail("? for shortcuts"), "idle");
});

test("a COMPLETED subagent row does not read as a live footer", () => {
  // The discriminator is the elapsed timer inside the brackets. A finished row carries a token count
  // and no timer, and must not hold an idle agent at working.
  assert.notEqual(classifyClaudeConsoleTail("+3 tool uses · ↓ 9.1k tokens"), "working");
  assert.notEqual(classifyClaudeConsoleTail("done · ↓ 9.1k tokens"), "working");
});

test("a token count without the bracketed timer is not a working signal", () => {
  // Prose is the thing this file must not let manufacture `working` — a false positive holds an idle
  // agent at working until the lease lapses.
  assert.notEqual(classifyClaudeConsoleTail("it used 12s and 3.4k tokens in total"), "working");
  assert.notEqual(classifyClaudeConsoleTail("↓ 34.7k tokens"), "working");
});

test("the newest signal still wins over an older one", () => {
  // The tail ACCUMULATES, so "contains" is the wrong question. A live footer BELOW an old idle prompt
  // means working; an idle prompt below a live footer means the turn ended.
  const footer = "✻ Concocting… (2m 1s · ↓ 5.0k tokens)";
  assert.equal(classifyClaudeConsoleTail(`? for shortcuts\n${footer}`), "working");
  assert.equal(classifyClaudeConsoleTail(`${footer}\n? for shortcuts`), "idle");
});
