// The compaction-dialog auto-confirm against REAL captured PTY bytes.
//
// WHY THIS FILE EXISTS. The auto-confirm (a4091bc) never fired once in production — agents sat
// at the dialog forever — and the suite stayed green the whole time, because the existing
// fixture (`COMPACT_DIALOG` in claude-console-prompts.test.js) is a hand-written string with
// real spaces and real newlines. Claude emits nothing of the sort: it paints each word at an
// absolute column with a CHA escape (`ESC[nG`) and steps down with `ESC[nB`. The old
// `stripAnsi` deleted those, collapsing the screen to
//     Resumingthefullsessionwillconsumeasubstantialportionofyourusagelimits.
// so every multi-word regex missed. A fixture that cannot fail is not a test.
//
// These are GENUINE bytes lifted from a live stuck console (terminal_sessions.output), escapes
// intact — 600B of pre-context, the dialog, then ~15.9KB of the spinner/OSC-title repaint noise
// that follows it. They reproduce BOTH production bugs, which is exactly why they are the
// fixture: the regex bug (words jammed together) AND the eviction bug (the dialog is pushed out
// of a small tail by the repaint flood). Fixing either alone leaves the agent stuck.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { matchConsolePrompt } from "../claude-console-prompts.js";
import { flattenConsoleText, stripAnsi } from "../claude-console-spinner.js";

const DOWN = "\x1b[B";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REAL_PTY = Buffer.from(
  fs.readFileSync(path.join(HERE, "fixtures", "claude-compaction-dialog.pty.b64"), "utf8").trim(),
  "base64",
).toString("utf8");

const atDialog = () => REAL_PTY.slice(0, REAL_PTY.indexOf("Esc to cancel") + 40);

test("the fixture is REAL: claude paints with cursor moves, not spaces", () => {
  assert.match(REAL_PTY, /Resuming\x1b\[\d+Gthe\x1b\[\d+Gfull/);
  // The old stripper is what broke it — this is the bug, pinned.
  assert.match(stripAnsi(REAL_PTY), /Resumingthefullsession/);
  assert.equal(stripAnsi(atDialog()).includes("substantial portion of your usage limits"), false);
});

test("flattenConsoleText reconstructs the screen's real words and lines", () => {
  const flat = flattenConsoleText(atDialog());
  assert.match(flat, /substantial portion of your usage limits/i);
  assert.match(flat, /Resume full session/i);
  assert.match(flat, /Resume from summary/i);
  assert.match(flat, /Enter to confirm/i);
  // The cursor-ROW safety check in computeCompactionConfirmAnswer splits on newlines. With the
  // old stripper there were ZERO, so "the cursor row" was the whole screen and the guard was
  // vacuous — it could not tell a highlighted option from the word appearing anywhere at all.
  assert.ok(flat.split("\n").length > 10, "flattened screen must have real lines");
});

test("REGRESSION: the auto-confirm now fires on the real dialog (it never did)", () => {
  const rule = matchConsolePrompt(atDialog(), { sessionMode: "managed" });
  assert.ok(rule, "matcher returned null on a dialog the agent is stuck at");
  assert.equal(rule.name, "compaction-resume-full-session");
  assert.deepEqual(rule.answer, [DOWN, "\r"], "select option 2: keep the full session context");
});

test("it selects 'Resume full session as-is' instead of compacting starting agents", () => {
  const rule = matchConsolePrompt(atDialog(), { sessionMode: "managed" });
  assert.deepEqual(rule.answer, [DOWN, "\r"]);
});

test("refuses to answer when no option is highlighted (prose, not a live menu)", () => {
  // Note the mutation is on the REAL bytes: the cursor glyph is painted as `❯\x1b[5G…1. …`, so
  // the option text is not even contiguous in the stream. Drop the glyph -> no live menu.
  const noCursor = atDialog().replace("❯", " ");
  assert.equal(matchConsolePrompt(noCursor, { sessionMode: "managed" }), null);
});

test("the cursor-row guard is now real: a NEWER menu below the stale dialog is not answered", () => {
  // The dangerous case: the compaction dialog has been answered and scrolled up, and a DIFFERENT
  // menu is now live below it. The compaction marker still matches (it is in the buffer), so the
  // only thing standing between us and typing Enter into the wrong menu is the cursor-ROW check —
  // which was VACUOUS before flattenConsoleText (zero newlines => "the cursor row" was the whole
  // screen, so it matched "Resume from summary" appearing anywhere at all).
  const laterMenu = atDialog() + "\r\x1b[2B❯\x1b[5G1. Delete everything\r\x1b[2B  2. Cancel\r\x1b[2BEnter to confirm";
  const rule = matchConsolePrompt(laterMenu, { sessionMode: "managed" });
  assert.equal(rule, null, "must not confirm: the live cursor row is not the summary option");
});

test("EVICTION: the dialog must survive the repaint flood that follows it", () => {
  // ~15.9KB of spinner + OSC-title noise arrives after the dialog. With the old 8192-byte tail
  // the prompt was gone before anything looked at it, so even a correct regex found nothing.
  const trailing = REAL_PTY.length - (REAL_PTY.indexOf("Esc to cancel") + 40);
  assert.ok(trailing > 8192, `fixture must carry a real flood (got ${trailing}B)`);

  const oldTail = REAL_PTY.slice(-8192);
  assert.equal(matchConsolePrompt(oldTail, { sessionMode: "managed" }), null, "old tail: evicted");

  // The bridge now drops OSC noise on append and keeps a 64KB window.
  const OSC = /\x1b\][^\x07]*(?:\x07|\x1b\\)/g;
  const newTail = REAL_PTY.replace(OSC, "").slice(-65536);
  const rule = matchConsolePrompt(newTail, { sessionMode: "managed" });
  assert.ok(rule, "dialog must still be reachable after the repaint flood");
  assert.equal(rule.name, "compaction-resume-full-session");
  assert.deepEqual(rule.answer, [DOWN, "\r"]);
});
