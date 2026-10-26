// An agent id becomes part of a FILE PATH in four modules, and nothing tested that it cannot escape.
//
// `claude-session-store.js`, `hermes-endpoint.js` and `hermes-loop-ready.js` each build (until 0.9.10 `hermes-daemon.js` too)
// a store or marker path from an agent id. Agent ids are not operator-typed constants: they arrive from
// `AIFY_AGENT_ID` in a spawned process's environment, from registration payloads, and from marker files
// written by other processes.
//
// FOUR PRIVATE COPIES BECAME TWO OWNERS in v0.5.4. The three hermes modules held byte-identical
// declarations; `hermes-endpoint.js` owns that one now and the other two import it. What did NOT change is
// the part below that matters: there are still two VARIANTS, because the claude store's is a different
// function that happens to share a name.
//
// CONTAINMENT HOLDS AND IS SAFE BY CONSTRUCTION RATHER THAN BY CONTRACT, which is why it is worth writing
// down. Both sanitiser variants strip `/` and `\`, and every caller embeds the result with a literal prefix
// — so the value is a filename INFIX, never a standalone path segment. Two independent properties, in two
// different files, neither previously stated. A single regex edit — adding `/` to an allowed set, or
// dropping a prefix — removes the containment with nothing to notice.
//
// UNIQUENESS DOES NOT HOLD, and that is a live defect this file pins rather than fixes: two agent ids that
// both pass registration can collapse onto one hermes marker. See the CURRENT DEFECT case below.
//
// THE TWO VARIANTS DIFFER and that is recorded rather than reconciled. The hermes one allows
// `[a-zA-Z0-9_-]` and strips leading/trailing hyphens; `claude-session-store.js` also allows `.` and does
// not strip. Both are contained; unifying them is a decision, not a cleanup, because the hermes value is a
// path segment and the claude value is a filename infix — different jobs with different safe alphabets.
// Deduplicating the three hermes copies did not touch that: it removed copies of ONE variant, and both
// declarations now carry a comment naming the other so the shared name cannot suggest a shared function.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { claudeSessionStorePath } from "../claude-session-store.js";
import { readSessionIdMarker, writeSessionIdMarker } from "../hermes-endpoint.js";

// Ids chosen to escape a directory if the sanitiser or the caller's prefix were weakened.
const HOSTILE = [
  "..",
  "../..",
  "../../etc/passwd",
  "a/../../b",
  "..\\..\\windows\\system32",
  "/absolute",
  "C:\\windows",
  ".",
  "....//",
  "a\nb",
  "a\u0000b",
  "x".repeat(300),
  "-leading-and-trailing-",
  "",
];

test("NO agent id can move the claude session store outside its directory", () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
  try {
    for (const id of HOSTILE) {
      const p = path.resolve(claudeSessionStorePath(id, base));
      assert.ok(p.startsWith(path.resolve(base) + path.sep),
        `agent id ${JSON.stringify(id)} escaped the store: ${p}`);
      assert.equal(path.dirname(p), path.resolve(base),
        `…and must stay directly inside it, not in a subdirectory`);
    }
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("NO agent id can make the hermes session marker land outside its temp dir", () => {
  // Driven through the real writer, so this covers the caller's prefix as well as the sanitiser — the
  // containment depends on both, and testing the regex alone would prove only half of it.
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
  try {
    for (const id of HOSTILE) {
      writeSessionIdMarker(id, "sid_hostile", { tempDir: base });
    }
    // Everything written must be a direct child of `base`, and `base` must contain no directories at all.
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      const full = path.join(dir, e.name);
      return e.isDirectory() ? [full, ...walk(full)] : [full];
    });
    for (const entry of walk(base)) {
      assert.equal(path.dirname(entry), base, `marker escaped into a subdirectory: ${entry}`);
    }
    assert.ok(fs.readdirSync(base, { withFileTypes: true }).every((e) => e.isFile()),
      "no directories may be created inside the marker dir");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("CURRENT DEFECT: two VALIDLY-REGISTERED ids can share one session marker", () => {
  // Pinned, not fixed — changing the sanitiser renames every existing marker file, which is a migration
  // decision rather than a code fix.
  //
  // `SAFE_NAME_RE` is /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/, so dots and trailing hyphens are legal in an
  // agent id. The hermes sanitiser maps `.` to `-` and strips trailing hyphens. So `a.b` and `a-b` — two
  // ids that both pass registration — resolve to the SAME marker key, as do `x` and `x-`.
  //
  // What that marker holds is the agent's REAL hermes session id, which its next launch resumes: the two
  // agents overwrite each other's binding, and one relaunches into the other's transcript with nothing in the
  // logs to say why. (Until 0.9.10 this was pinned on the gateway-URL marker, which shared the sanitiser.)
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
  try {
    writeSessionIdMarker("a.b", "sid_dotted", { tempDir: base });
    writeSessionIdMarker("a-b", "sid_hyphened", { tempDir: base });
    const files = fs.readdirSync(base).filter((n) => n.startsWith("aify-hermes-session-"));
    assert.equal(files.length, 1, "CURRENT: the two ids collapse onto one marker file");
    assert.equal(readSessionIdMarker("a.b", { tempDir: base }), "sid_hyphened",
      "CURRENT: `a.b` reads the marker `a-b` wrote");

    // The trailing-hyphen pair, same cause.
    const base2 = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
    try {
      writeSessionIdMarker("victim", "sid_victim", { tempDir: base2 });
      assert.equal(readSessionIdMarker("victim-", { tempDir: base2 }), "sid_victim",
        "CURRENT: a trailing hyphen collapses onto the bare id");
    } finally {
      fs.rmSync(base2, { recursive: true, force: true });
    }
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("ids that differ beyond the sanitiser's alphabet still stay distinct", () => {
  // The other direction, so the finding above is scoped rather than open-ended: ordinary distinct ids do
  // NOT collide, and case is preserved in the key even where the filesystem may not honour it.
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
  try {
    writeSessionIdMarker("agent-one", "sid_one", { tempDir: base });
    writeSessionIdMarker("agent-two", "sid_two", { tempDir: base });
    assert.equal(readSessionIdMarker("agent-one", { tempDir: base }), "sid_one");
    assert.equal(readSessionIdMarker("agent-two", { tempDir: base }), "sid_two");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("the store path is stable for one id and distinct between ids", () => {
  // Anti-vacuity. A sanitiser that mapped everything to a constant would satisfy containment perfectly and
  // be catastrophic — every agent would share one session file.
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
  try {
    assert.equal(claudeSessionStorePath("agent-a", base), claudeSessionStorePath("agent-a", base),
      "the same id must map to the same path");
    assert.notEqual(claudeSessionStorePath("agent-a", base), claudeSessionStorePath("agent-b", base),
      "different ids must NOT collapse onto one file");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("CURRENT: the two sanitiser variants disagree, and both are contained", () => {
  // Recorded so the difference is deliberate rather than discovered. The hermes trio maps `.` to `-`; the
  // claude store keeps it. Both are safe for their own caller — a path segment and a filename infix — and
  // unifying them would need a decision about which alphabet is right for which job.
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-contain-"));
  try {
    const dotted = claudeSessionStorePath("a.b", base);
    assert.match(path.basename(dotted), /a\.b/, "the claude store preserves dots in an id");
    writeSessionIdMarker("a.b", "sid_x", { tempDir: base });
    const names = fs.readdirSync(base).filter((n) => n.startsWith("aify-hermes-session-"));
    assert.equal(names.length, 1);
    assert.doesNotMatch(names[0], /a\.b/, "the hermes marker replaces dots");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});
