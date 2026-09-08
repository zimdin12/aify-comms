#!/usr/bin/env node
// A skill that names a file or a function must name one that exists.
//
// WHY THIS IS WORTH A GATE. `SKILL.md` and its references load into every agent's context every
// session, and they are read as INSTRUCTIONS -- an agent handed `service/api_core/console_prompts.py`
// goes and opens it. A pointer that no longer resolves does not fail loudly: the agent finds nothing,
// concludes the thing it was told about is absent, and reasons from that.
//
// AND THIS REPO HAS BEEN CAUGHT BY IT TWICE ALREADY, in this version alone. Two paragraphs in
// `dispatch-bridges.md` described a `channel-enter` mechanism present in NO source file, and a Deploy
// line told operators to re-run `install.sh` for a change that lives inside the container. Both read
// perfectly. Both were written beside work that then moved.
//
// PROSE IS NOT CHECKED HERE, and cannot be. What this checks is the part that is mechanical: the
// backticked names. A skill can still describe a rule that no longer holds -- that is what a reviewer
// is for -- but it can no longer point at a file nobody has.
//
// EXTERNAL NAMES ARE DECLARED, NOT GUESSED. These skills document hermes and codex internals on
// purpose, because that is where the failures live; those names are not ours to have. The list below
// is short, each entry says whose it is, and a name that drops off it fails here rather than quietly
// becoming unchecked.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const REPO = path.resolve(new URL("../../..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const SKILL_ROOTS = [path.join(REPO, ".claude", "skills"), path.join(REPO, ".agents", "skills")];
const SOURCE_ROOTS = ["service", "mcp", "scripts", "config"];
const LOOSE_FILES = ["install.sh", "redeploy.sh", "setup.sh"];
const PRUNE = new Set(["node_modules", ".git", "__pycache__", ".pytest_cache", ".venv", "venv"]);

/**
 * Names these skills document on purpose that belong to somebody else.
 *
 * Each says WHOSE, because "external" with no owner is how a name that used to be ours gets filed
 * here after it is deleted. `fetch` and `spawn` are platform builtins the prose names while
 * explaining a mechanism; the rest are hermes' and codex' own internals, which is exactly what a
 * debugging skill for those runtimes has to talk about.
 */
const NOT_OURS = new Map([
  ["fetch", "the platform builtin"],
  ["spawn", "node:child_process"],
  ["derive", "named as a concept in the status model, not as an export"],
  ["web_server.py", "hermes"],
  ["discover_mcp_tools", "hermes"],
  ["adapters/claude.js", "hermes"],
  ["api_v2.py", "the router this repo retired; the reference is historical and says so"],
  // Written as an ILLUSTRATION -- "e.g. an `observability` plugin's `install-deps.js`" -- of a
  // third-party hermes plugin's own file. Not ours, and never was. Flagged by this gate on its first
  // run, which is the gate working: a reader cannot tell an example from a location we own.
  ["install-deps.js", "an example of a third-party hermes plugin's file"],
]);

/** Every source file this repo actually has, as full repo-relative paths and as basenames. */
function sourceIndex() {
  const paths = new Set();
  const basenames = new Set();
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (PRUNE.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) { walk(full); continue; }
      paths.add(path.relative(REPO, full).split(path.sep).join("/"));
      basenames.add(entry.name);
    }
  };
  for (const root of SOURCE_ROOTS) {
    const full = path.join(REPO, root);
    if (fs.existsSync(full)) walk(full);
  }
  for (const loose of LOOSE_FILES) {
    if (fs.existsSync(path.join(REPO, loose))) { paths.add(loose); basenames.add(loose); }
  }
  return { paths, basenames };
}

/** Every backticked file name and `thing()` call across the skills, with where it was written. */
function skillPointers() {
  const found = new Map();
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) { walk(full); continue; }
      if (!entry.name.endsWith(".md")) continue;
      const text = fs.readFileSync(full, "utf8");
      const where = path.relative(REPO, full).split(path.sep).join("/");
      const add = (token) => {
        if (!found.has(token)) found.set(token, new Set());
        found.get(token).add(where);
      };
      for (const m of text.matchAll(/`([A-Za-z_][A-Za-z0-9_./-]*\.(?:py|js|mjs|sh))`/g)) add(m[1]);
      for (const m of text.matchAll(/`([A-Za-z_][A-Za-z0-9_]{2,})\(\)`/g)) add(m[1]);
    }
  };
  for (const root of SKILL_ROOTS) if (fs.existsSync(root)) walk(root);
  return found;
}

/** Does any source file define or contain this identifier? */
function definedSomewhere(name, index) {
  for (const relative of index.paths) {
    if (!/\.(py|js|mjs)$/.test(relative)) continue;
    // TESTS DO NOT COUNT AS AN IMPLEMENTATION. A name that appears only in a test file is a name
    // nothing implements, which is precisely what this check exists to find -- and without this the
    // sweep matched THIS FILE, so its own negative control found the identifier it had invented to
    // prove nothing existed. The file check above still indexes tests, because a skill naming a test
    // file is naming something real.
    if (relative.includes("/tests/")) continue;
    let text = "";
    try { text = fs.readFileSync(path.join(REPO, relative), "utf8"); } catch { continue; }
    if (text.includes(name)) return relative;
  }
  return "";
}

test("POSITIVE CONTROL: the scan finds pointers, and the index finds source", () => {
  // Both halves can fail silently to nothing. An empty pointer set makes every assertion below
  // vacuous, and an empty index makes them all fail for the wrong reason.
  const pointers = skillPointers();
  const index = sourceIndex();
  assert.ok(pointers.size > 20, `only ${pointers.size} pointers found across the skills`);
  assert.ok(index.paths.size > 200, `only ${index.paths.size} source files indexed`);
  assert.ok(index.basenames.has("control_plane.py"), "the index cannot see a file that certainly exists");
});

test("EVERY FILE A SKILL NAMES IS A FILE THIS REPO HAS", () => {
  // A path with a slash is checked as a PATH -- `service/db.py` must be at that location, not merely
  // somewhere -- because a skill that gives a location is telling an agent where to look.
  const index = sourceIndex();
  const missing = [];
  for (const [token, where] of skillPointers()) {
    if (!/\.(py|js|mjs|sh)$/.test(token)) continue;
    if (NOT_OURS.has(token)) continue;
    // A PARTIAL PATH RESOLVES BY SUFFIX. `api_core/registration_gates.py` is how the skills write
    // that file and it is legible; requiring the full `service/` prefix reported an existing file as
    // missing. Still strict enough to catch a wrong directory -- `wrong_dir/registration_gates.py`
    // matches nothing -- which is the part worth checking.
    const ok = token.includes("/")
      ? [...index.paths].some((relative) => relative === token || relative.endsWith(`/${token}`))
      : index.basenames.has(token);
    if (!ok) missing.push(`${token} (named in ${[...where].join(", ")})`);
  }
  assert.deepEqual(missing, [],
    `these files are named by a skill and do not exist:\n  ${missing.join("\n  ")}\n`
    + "Either the file moved and the skill should say where, or the mechanism is gone and the "
    + "paragraph should go with it. Adding the name to NOT_OURS is only right if it is somebody "
    + "else's file.");
});

test("EVERY FUNCTION A SKILL NAMES APPEARS IN SOURCE", () => {
  // Looser than the file check on purpose: prose names a function to explain a mechanism, and where
  // it lives is not always the point. What must be true is that the name exists at all -- a skill
  // naming a function nobody has is describing behaviour nobody implements, which is the exact shape
  // of the two paragraphs deleted from `dispatch-bridges.md` this version.
  const index = sourceIndex();
  const missing = [];
  for (const [token, where] of skillPointers()) {
    if (/\.(py|js|mjs|sh)$/.test(token)) continue;
    if (NOT_OURS.has(token)) continue;
    if (!definedSomewhere(token, index)) missing.push(`${token}() (named in ${[...where].join(", ")})`);
  }
  assert.deepEqual(missing, [],
    `these functions are named by a skill and appear in no source file:\n  ${missing.join("\n  ")}`);
});

test("NEGATIVE CONTROL: an invented name would be caught", () => {
  // Both sweeps above pass if the resolver said yes to everything. These are the answers that make
  // a green run mean something.
  const index = sourceIndex();
  assert.ok(!index.basenames.has("a-file-nobody-has.py"));
  assert.ok(!index.paths.has("service/a-file-nobody-has.py"));
  assert.equal(definedSomewhere("aFunctionNobodyDefines_xyzzy", index), "");
  // And the resolver CAN say yes, or the line above proves nothing.
  assert.ok(definedSomewhere("rowResumeKey", index), "the resolver cannot find a function that exists");
});

test("THE EXTERNAL LIST STAYS HONEST: every entry is still named by a skill", () => {
  // A name that stops being mentioned should leave this list rather than sit here exempting nothing.
  // That is the same rule `oversized-allowlist.json` follows, and for the same reason: a list nobody
  // prunes becomes a place to put things.
  const pointers = skillPointers();
  const unused = [...NOT_OURS.keys()].filter((name) => !pointers.has(name));
  assert.deepEqual(unused, [],
    `these names are declared external and no skill mentions them any more: ${unused.join(", ")}`);
});
