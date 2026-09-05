#!/usr/bin/env node
// A destructive tool's description IS its safety mechanism, so it must be pinned.
//
// EXTERNAL REVIEW, Round 9 LOW: "the three safety sentences are pinned by no test". Checked and
// true. `lifecycle-tools.mjs`'s own header says "The tests below assert those warnings survive,
// because for `comms_clear` the description IS the safety mechanism" -- and no test asserted any of
// it. The reviewer has instead re-read seventeen files of hunks BY HAND each round to confirm no
// safety sentence was cut, which is the work this file exists to stop repeating.
//
// `comms_clear` says it outright: "There is no undo and no confirmation prompt; the only safety is
// this sentence." A model reads that description before deciding to call the tool. Trimming it for
// brevity, or paraphrasing it into something calmer, silently removes the only thing standing
// between a tidy-up impulse and wiping every message, artifact and identity on the server -- other
// teams included.
//
// THE POPULATION IS DERIVED, not listed. Any tool whose description announces itself as DESTRUCTIVE
// is covered, so a fifth destructive tool is governed the day it lands rather than the day somebody
// remembers to add it here. What each one must SAY is pinned per tool, because the specific promise
// differs and a generic "mentions the word destructive" check would pass on a description that had
// lost everything else.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const STDIO = join(HERE, "..");

/** Every `server.tool("name", "description...")` in the bridge's tool modules. */
function toolDescriptions() {
  const found = new Map();
  for (const name of readdirSync(STDIO).filter((f) => f.endsWith(".mjs") || f.endsWith(".js"))) {
    const text = readFileSync(join(STDIO, name), "utf8");
    const re = /server\.tool\(\s*"([a-z_]+)"\s*,\s*([\s\S]*?)\n\s*\{/g;
    let match;
    while ((match = re.exec(text)) !== null) {
      found.set(match[1], { file: name, description: match[2] });
    }
  }
  return found;
}

/** Tools that announce themselves as destructive. The population, derived from what they say. */
function destructiveTools() {
  const out = new Map();
  for (const [name, entry] of toolDescriptions()) {
    if (/DESTRUCTIVE|MOST DESTRUCTIVE/.test(entry.description)) out.set(name, entry);
  }
  return out;
}

//: What each destructive tool must still promise. Phrases, not whole paragraphs: the wording around
//: them may be improved, and pinning a full description would make every edit a test failure and
//: teach the next author to relax the gate.
const REQUIRED = {
  comms_clear: [
    "DESTRUCTIVE AND IRREVERSIBLE",
    "WHOLE hub",
    "no undo",
    "the only safety is this sentence",
  ],
  comms_remove_agent: ["DESTRUCTIVE"],
  comms_channel_delete: ["no undo", "ends it for everybody"],
  // PINNED ELSEWHERE, and pointed at rather than copied. `compact-mode-contract.test.js` already
  // holds four contracts on this description (`/DESTRUCTIVE/`, `/DESTRUCTIVE TO CONTEXT/`,
  // `/record open decisions somewhere durable FIRST/`, `/durable|write/`). Repeating them here would
  // be one meaning in two files, agreeing until somebody edits one -- the exact shape this repo keeps
  // getting caught by. What this map guarantees is that every destructive tool is ACCOUNTED FOR.
  comms_compact: { pinnedBy: "compact-mode-contract.test.js" },
};

test("THE SCAN FINDS THE DESTRUCTIVE TOOLS AT ALL", () => {
  // POSITIVE CONTROL. Every assertion below reads "this tool's description contains X", and a broken
  // parser finds no tools -- which satisfies an `every` check perfectly and reports green.
  const tools = toolDescriptions();
  assert.ok(tools.size > 20, `the tool scan found only ${tools.size} tools; the parser is not reaching them`);
  const destructive = destructiveTools();
  assert.ok(
    destructive.size >= 3,
    `only ${destructive.size} destructive tool(s) found: ${[...destructive.keys()].join(", ")}`,
  );
  assert.ok(destructive.has("comms_clear"), "comms_clear is the one this file exists for");
});

test("EVERY DESTRUCTIVE TOOL STILL CARRIES ITS WARNING", () => {
  const destructive = destructiveTools();
  const missing = [];
  for (const [name, phrases] of Object.entries(REQUIRED)) {
    const entry = destructive.get(name);
    if (!entry) {
      missing.push(`${name}: no longer declares itself DESTRUCTIVE`);
      continue;
    }
    if (!Array.isArray(phrases)) continue;   // accounted for by the test named in the map
    for (const phrase of phrases) {
      if (!entry.description.includes(phrase)) missing.push(`${name} (${entry.file}): lost "${phrase}"`);
    }
  }
  assert.deepEqual(
    missing,
    [],
    "a destructive tool's description lost a load-bearing warning. A model reads this text before "
    + "deciding to call the tool, and for comms_clear the description IS the safety mechanism -- "
    + "there is no undo and no confirmation prompt behind it.\n  " + missing.join("\n  "),
  );
});

test("a NEW destructive tool cannot arrive unpinned", () => {
  // The gap that made this file necessary in the first place: a rule nobody enforces holds until the
  // next tool. Anything that calls itself destructive must appear in REQUIRED, so adding one is a
  // decision made here rather than a silence.
  const unpinned = [...destructiveTools().keys()].filter((name) => !(name in REQUIRED));
  assert.deepEqual(
    unpinned,
    [],
    `${unpinned.join(", ")} announce themselves as DESTRUCTIVE and no phrase is pinned for them. `
    + "Add what the description must keep saying, or say here why it needs nothing.",
  );
});

test("a pointer to another test names a file that exists", () => {
  // A pointer nobody can resolve is worse than a missing pin: it reads as coverage. `blast-radius`
  // makes this same point about claims that something is tested elsewhere.
  for (const [name, entry] of Object.entries(REQUIRED)) {
    if (Array.isArray(entry)) continue;
    const target = join(HERE, entry.pinnedBy);
    assert.ok(
      readdirSync(HERE).includes(entry.pinnedBy),
      `${name} is recorded as pinned by ${entry.pinnedBy}, which does not exist in ${HERE}`,
    );
    assert.match(
      readFileSync(target, "utf8"),
      new RegExp(name),
      `${entry.pinnedBy} does not mention ${name}, so the pointer is stale`,
    );
  }
});

test("the module that CLAIMS these tests exist can still point at them", () => {
  // `lifecycle-tools.mjs` says "The tests below assert those warnings survive". It said that while
  // nothing did -- a claim about coverage with nothing behind it, which is worse than no claim
  // because a reader stops looking. This keeps the two in step.
  const text = readFileSync(join(STDIO, "lifecycle-tools.mjs"), "utf8");
  assert.match(
    text,
    /the description IS the safety mechanism/,
    "lifecycle-tools.mjs no longer explains why its warnings are load-bearing; if that reasoning "
    + "moved, this file should point at wherever it went",
  );
});
