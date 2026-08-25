#!/usr/bin/env node
// Nothing in the bridge reacts to an error that cannot arrive.
//
// A function that catches without re-throwing returns normally on failure. A caller that wraps it in
// `try { ... } catch { react }` never reacts, and the reaction is dead. Both halves read as careful,
// which is why review misses it.
//
// The dashboard grew the same gate first, after the class appeared there three times. This is the
// higher-stakes half: 162 files, 235 functions that swallow every error, and the code that stops
// processes lives here. A fallback kill that cannot run is a worker that survives a stop, which is
// this repo's most expensive failure family — the reaper incidents, the restart that produced no
// worker, the grandchildren that outlived a TaskStop.
//
// MEASURED, and the honest result: the sweep found exactly two sites and NEITHER was a defect.
// terminal-runtime.js wrapped terminateProcessTree in `catch { state.term?.kill(); }`, and that
// function's own last act is `proc.kill(signal)` on the very object the fallback would retry. So the
// fallback duplicated a call that had already happened — and in the self-protect branch its
// unreachability was protective, because terminateProcessTree REFUSES a self-protected pid and a
// reachable fallback would have killed the bridge anyway. The duplicates are gone; the `try` stays so
// a future throw cannot escape into a timer callback.
//
// The gate is here so the next one is caught mechanically rather than by someone reading well.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BRIDGE = join(dirname(fileURLToPath(import.meta.url)), "..");

const SOURCES = Object.fromEntries(
  readdirSync(BRIDGE)
    .filter((f) => (f.endsWith(".mjs") || f.endsWith(".js")) && !f.includes(".test."))
    .map((f) => [f, readFileSync(join(BRIDGE, f), "utf8")]),
);

/** Functions that catch and never re-throw: calling one cannot fail. */
function swallowers() {
  const found = {};
  for (const [file, text] of Object.entries(SOURCES)) {
    for (const m of text.matchAll(/(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/g)) {
      const ends = [text.indexOf("\nfunction ", m.index + 1), text.indexOf("\nexport ", m.index + 1)]
        .filter((i) => i !== -1);
      const body = text.slice(m.index, ends.length ? Math.min(...ends) : text.length);
      if (/catch\s*[({]/.test(body) && !/\bthrow\b/.test(body)) found[m[1]] ??= file;
    }
  }
  return found;
}

/** The catch's OWN braces, balanced — not the last brace on the line. */
function catchBody(after) {
  const open = after.indexOf("{");
  if (open === -1) return null;
  let depth = 0;
  for (let k = open; k < after.length; k += 1) {
    if (after[k] === "{") depth += 1;
    else if (after[k] === "}") {
      depth -= 1;
      if (depth === 0) return after.slice(open + 1, k);
    }
  }
  return null;
}

function deadReactions() {
  const swallows = swallowers();
  const out = [];
  for (const [file, text] of Object.entries(SOURCES)) {
    text.split(String.fromCharCode(10)).forEach((line, i) => {
      const m = /try\s*\{\s*(?:await\s+)?([A-Za-z_$][\w$]*)\(/.exec(line);
      if (!m || !(m[1] in swallows)) return;
      const tail = line.slice(m.index + m[0].length);
      if (!tail.includes("catch")) return;
      const body = catchBody(tail.slice(tail.indexOf("catch")));
      if (body === null) return;
      const acting = body.replace(/\/\*[\s\S]*?\*\//g, "").trim();
      if (acting) out.push(`${file}:${i + 1} reacts to ${m[1]}() which cannot fail -> ${acting.slice(0, 60)}`);
    });
  }
  return out;
}

// ── controls ───────────────────────────────────────────────────────────────────────────────────
{
  const s = swallowers();
  assert.ok(
    Object.keys(s).length > 50,
    `only ${Object.keys(s).length} swallowers found across ${Object.keys(SOURCES).length} files; the sweep is broken`,
  );
  assert.ok(Object.keys(SOURCES).length > 100, "the file scan found almost nothing");
  assert.ok(!("zzzNotAFunction" in s), "the sweep invents names");
}

// ── an empty catch and a comment-only catch are exempt by design ────────────────────────────────
{
  assert.equal(catchBody("catch {}").trim(), "", "an empty catch must read as empty");
  assert.equal(
    catchBody("catch { /* gone */ }").replace(/\/\*[\s\S]*?\*\//g, "").trim(), "",
    "a comment-only catch must read as a documented no-op",
  );
  // The bug the dashboard version shipped with: lastIndexOf ran past the catch into the enclosing
  // block, so an empty catch inside an `if` looked like a live reaction and the gate failed on
  // correct code.
  assert.equal(catchBody("catch {} }").trim(), "", "the extractor ran past the catch's own braces");
}

// ── the sweep itself ───────────────────────────────────────────────────────────────────────────
{
  const offenders = deadReactions();
  assert.deepEqual(
    offenders, [],
    "these catches react to an error the callee already swallowed: " + offenders.join("; "),
  );
}

console.log("dead-error-reactions.test.js: all assertions passed");
