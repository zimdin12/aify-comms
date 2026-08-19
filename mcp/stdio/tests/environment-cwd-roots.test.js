// The environment's effective cwd roots — two decisions out of server.js, called for the first time.
//
// server.js exports NOTHING and, until the v0.6 boot guard, could not even be imported without
// registering a bridge. So every decision inside it has always been unreachable. These two are small,
// but they gate which working directories an environment will host work in: parse them wrong and the
// bridge either advertises roots it cannot serve or silently drops the ones it can — and the symptom is
// a spawn refused with no obvious cause.
//
// THE DISTINCTION WORTH THE TEST is `null` vs `[]`. "The service said nothing about roots" and "the
// service said there are none" are different facts, and collapsing them lets a malformed response erase
// a working configuration. Nothing in the suite has ever asserted that difference.

import assert from "node:assert/strict";
import test from "node:test";

const { parseEffectiveCwdRoots, withEffectiveCwdRoots } =
  await import("../environment-cwd-roots.mjs");

// ── parsing a heartbeat response ────────────────────────────────────────────────────────────

test("roots come back trimmed, with empties dropped", () => {
  const roots = parseEffectiveCwdRoots({
    environment: { cwdRoots: ["  /work  ", "/other", "", "   "] },
  });
  assert.deepEqual(roots, ["/work", "/other"],
    "an untrimmed root does not match the cwd a spawn asks for, and an empty one matches everything");
});

test("a response that says NOTHING about roots returns null, not an empty list", () => {
  for (const response of [{}, { environment: {} }, null, undefined, { environment: null }]) {
    assert.equal(
      parseEffectiveCwdRoots(response), null,
      `${JSON.stringify(response)} should mean "the service did not tell us" — returning [] instead `
      + "would let the caller overwrite a working root list with nothing",
    );
  }
});

test("a non-array cwdRoots is treated as no answer, not as an answer", () => {
  for (const bad of ["/work", 42, {}, true]) {
    assert.equal(parseEffectiveCwdRoots({ environment: { cwdRoots: bad } }), null,
      `cwdRoots=${JSON.stringify(bad)} is malformed; treating it as data would store garbage`);
  }
});

test("an EMPTY array is an answer — the service said there are none", () => {
  assert.deepEqual(
    parseEffectiveCwdRoots({ environment: { cwdRoots: [] } }), [],
    "this is the case that must NOT collapse into null: the service answered, and the answer was none",
  );
});

test("non-string entries are coerced rather than crashing the heartbeat", () => {
  // The heartbeat's catch swallows everything, so a throw here would look like a network failure and
  // retry forever against a response that will never parse.
  assert.deepEqual(parseEffectiveCwdRoots({ environment: { cwdRoots: [null, 0, "/ok"] } }), ["/ok"]);
});

// ── applying them to a payload ──────────────────────────────────────────────────────────────

test("roots replace whatever the payload carried", () => {
  const payload = { id: "env-1", cwdRoots: ["/stale"] };
  assert.deepEqual(
    withEffectiveCwdRoots(payload, ["/fresh"]),
    { id: "env-1", cwdRoots: ["/fresh"] },
  );
});

test("NO roots leaves the payload exactly as it was", () => {
  const payload = { id: "env-1", cwdRoots: ["/configured"] };
  for (const nothing of [null, undefined, []]) {
    assert.deepEqual(
      withEffectiveCwdRoots(payload, nothing), payload,
      `roots=${JSON.stringify(nothing)} must not blank out the payload's own configuration`,
    );
  }
});

test("the payload is never MUTATED", () => {
  // The caller builds a fresh payload today, so a mutation would be invisible — right up until somebody
  // reuses one, at which point the bug is in a different file from the cause.
  const payload = { id: "env-1", cwdRoots: ["/original"] };
  const before = JSON.stringify(payload);
  withEffectiveCwdRoots(payload, ["/replacement"]);
  assert.equal(JSON.stringify(payload), before, "withEffectiveCwdRoots mutated its argument");
});

test("every other field survives the merge", () => {
  const payload = { id: "env-1", machineId: "win32:box", os: "windows", runtimes: [{ runtime: "codex" }] };
  const merged = withEffectiveCwdRoots(payload, ["/w"]);
  assert.equal(merged.machineId, "win32:box",
    "the payload is what registers this environment; dropping machineId would register it as a "
    + "different environment entirely");
  assert.equal(merged.os, "windows");
  assert.deepEqual(merged.runtimes, [{ runtime: "codex" }]);
});

// ── the two together, as the heartbeat uses them ────────────────────────────────────────────

test("a heartbeat that answers with roots ends up applying exactly those", () => {
  const response = { environment: { cwdRoots: [" /a ", "/b", ""] } };
  const roots = parseEffectiveCwdRoots(response);
  assert.deepEqual(withEffectiveCwdRoots({ id: "e" }, roots), { id: "e", cwdRoots: ["/a", "/b"] });
});

test("a heartbeat that answers with NOTHING leaves the previous roots in force", () => {
  // The sequence that matters operationally: roots learned once, then a response that omits them.
  let remembered = parseEffectiveCwdRoots({ environment: { cwdRoots: ["/w"] } });
  const second = parseEffectiveCwdRoots({ ok: true });
  if (second !== null) remembered = second;
  assert.deepEqual(remembered, ["/w"],
    "a response without a cwdRoots field wiped the roots learned from an earlier heartbeat");
});
