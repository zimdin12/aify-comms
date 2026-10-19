#!/usr/bin/env node
// Tests that CALL `codex-thread-selection.js` — which codex conversation a bridge resumes.
//
// All three were module-private in `runtimes-codex.js` until v0.5.4 and therefore unreachable from a
// test. Picking the wrong thread does not raise: the agent resumes somebody else's conversation and
// the only symptom is a reply that makes no sense in context.
//
// THE TWO RULES THIS PINS:
//   * ONLY THIS WORKSPACE — a thread whose cwd matches this workspace wins over a more recent one
//     that does not, and when none matches the answer is "", never the newest thread elsewhere
//     (that fallback bound an agent to the operator's own conversation on 2026-10-19).
//   * PATHS COMPARE NORMALISED — Codex stores Windows cwds with backslashes while the bridge passes
//     forward slashes, so a literal `===` fell through and the cwd branch never fired. That bug is
//     recorded in the function's own comment; here it is a failing assertion if it comes back.

import assert from "node:assert/strict";

import {
  normalizePathForCompare,
  parseTimestamp,
  pickNewestCodexThreadId,
} from "../codex-thread-selection.js";

// ── parseTimestamp: three input shapes, one number, and 0 for "unknown" ──────────────────────
{
  assert.equal(parseTimestamp(1700000000000), 1700000000000, "a finite number passes through");
  assert.equal(parseTimestamp("1700000000000"), 1700000000000, "a numeric string is a number");
  assert.equal(parseTimestamp("2026-08-15T10:00:00Z"), Date.parse("2026-08-15T10:00:00Z"),
    "an ISO string is parsed");

  // 0 rather than NaN is what keeps the sort total: NaN comparisons are false in both directions,
  // so a single unparseable timestamp would make the ordering depend on the input order.
  for (const unusable of ["", "   ", "not a date", null, undefined, {}, []]) {
    assert.equal(parseTimestamp(unusable), 0, `${JSON.stringify(unusable)} is 0, never NaN`);
  }
  assert.equal(parseTimestamp(NaN), 0, "NaN itself is not finite and becomes 0");
  assert.equal(parseTimestamp(-5), -5, "a negative number is still a number — clamping is not this one's job");
  // A NEGATIVE NUMERIC STRING FALLS THROUGH TO `Date.parse`, which reads "-5" as a DATE — May 2001
  // on this engine. Documented, not endorsed: the `numeric > 0` guard rejects it as an epoch and the
  // next line accepts it as something else entirely. It only matters if codex ever emits such a
  // value, and the ordering it would produce is "older than everything real", which is harmless
  // here. Pinned so the oddity is a recorded fact rather than a surprise to whoever meets it next.
  assert.equal(parseTimestamp("-5"), Date.parse("-5"), "a negative string is date-parsed, not rejected");
  assert.ok(parseTimestamp("-5") > 0, "and the result is a positive epoch, which is the surprising part");
}

// ── normalizePathForCompare: the fix for the backslash bug ───────────────────────────────────
{
  assert.equal(normalizePathForCompare("C:\\Work\\Repo"), "c:/work/repo");
  assert.equal(normalizePathForCompare("C:/Work/Repo/"), "c:/work/repo", "a trailing slash is dropped");
  assert.equal(normalizePathForCompare("C:/Work/Repo///"), "c:/work/repo", "and so are several");
  assert.equal(normalizePathForCompare("  C:/Work/Repo  "), "c:/work/repo", "surrounding space is trimmed");
  assert.equal(
    normalizePathForCompare("C:\\Work\\Repo"),
    normalizePathForCompare("c:/work/repo/"),
    "the two spellings the bug was about must compare EQUAL",
  );
  assert.equal(normalizePathForCompare(""), "");
  assert.equal(normalizePathForCompare(null), "");
}

// ── pickNewestCodexThreadId ──────────────────────────────────────────────────────────────────
const thread = (id, over = {}) => ({ id, cwd: "C:/work/repo", updatedAt: 1, ...over });

{
  assert.equal(pickNewestCodexThreadId(null, "C:/work/repo"), "", "no result is no thread");
  assert.equal(pickNewestCodexThreadId({}, "C:/work/repo"), "");
  assert.equal(pickNewestCodexThreadId({ threads: [] }, "C:/work/repo"), "");

  // Both list shapes the codex API has returned.
  assert.equal(pickNewestCodexThreadId({ threads: [thread("t1")] }, "C:/work/repo"), "t1");
  assert.equal(pickNewestCodexThreadId({ data: [thread("t2")] }, "C:/work/repo"), "t2",
    "`data` is accepted as well as `threads`");

  // Recency within the matching tier.
  const newest = pickNewestCodexThreadId(
    { threads: [thread("old", { updatedAt: 10 }), thread("new", { updatedAt: 20 })] },
    "C:/work/repo",
  );
  assert.equal(newest, "new");

  // PREFERRED BEATS NEWEST — the rule that makes this correct rather than merely recent.
  const preferred = pickNewestCodexThreadId(
    {
      threads: [
        thread("elsewhere-but-newer", { cwd: "C:/other/place", updatedAt: 99 }),
        thread("here-but-older", { cwd: "C:/work/repo", updatedAt: 1 }),
      ],
    },
    "C:/work/repo",
  );
  assert.equal(preferred, "here-but-older",
    "a matching cwd outranks a more recent thread from a different workspace");

  // THE BACKSLASH CASE, end to end.
  const windowsCwd = pickNewestCodexThreadId(
    {
      threads: [
        thread("elsewhere", { cwd: "C:/other", updatedAt: 99 }),
        thread("mine", { cwd: "C:\\Work\\Repo", updatedAt: 1 }),
      ],
    },
    "C:/work/repo",
  );
  assert.equal(windowsCwd, "mine",
    "a backslash cwd from Codex must match a forward-slash cwd from the bridge — this is the bug");

  // WAS "b" (the newest overall). Changed 2026-10-20: with no cwd match there is no thread to bind.
  // `thread/list` lists every rollout on the machine, and this fallback bound a fresh agent to the
  // operator's own Codex Desktop conversation on 2026-10-19. This is the assertion that goes red if
  // the fallback returns.
  const noMatch = pickNewestCodexThreadId(
    { threads: [thread("a", { cwd: "C:/x", updatedAt: 1 }), thread("b", { cwd: "C:/y", updatedAt: 9 })] },
    "C:/work/repo",
  );
  assert.equal(noMatch, "", "no cwd match binds nothing, never the newest thread from another workspace");

  // An absent cwd on either side cannot manufacture a match. WAS "a" (a fallback candidate); changed
  // 2026-10-20 for the same reason: a thread with no cwd cannot be shown to belong to this workspace.
  assert.equal(
    pickNewestCodexThreadId({ threads: [thread("a", { cwd: "", updatedAt: 5 })] }, "C:/work/repo"),
    "",
    "a thread with no cwd is not this workspace's thread",
  );
  assert.equal(
    pickNewestCodexThreadId({ threads: [thread("a", { updatedAt: 5 })] }, ""),
    "",
    "an empty workspace cwd matches nothing, even a thread that has a cwd",
  );
  assert.equal(
    pickNewestCodexThreadId({ threads: [thread("a", { cwd: "", updatedAt: 5 })] }, ""),
    "",
    "two empty cwds are not a match: neither names a workspace",
  );

  // Alternative cwd field names the API has used.
  for (const field of ["directory", "worktree"]) {
    const picked = pickNewestCodexThreadId(
      {
        threads: [
          thread("elsewhere", { cwd: "C:/other", updatedAt: 99 }),
          { id: "mine", [field]: "C:/work/repo", updatedAt: 1 },
        ],
      },
      "C:/work/repo",
    );
    assert.equal(picked, "mine", `\`${field}\` is honoured as the thread's directory`);
  }

  // Threads with no id are skipped rather than returned as "". The workspace cwd WAS "" here; changed
  // 2026-10-20 to this workspace's, because an empty cwd now matches nothing and the case would no
  // longer reach the id check it exists to pin.
  assert.equal(
    pickNewestCodexThreadId(
      { threads: [{ id: "", cwd: "C:/work/repo", updatedAt: 99 }, thread("real", { updatedAt: 1 })] },
      "C:/work/repo",
    ),
    "real",
  );

  // Timestamp field precedence: updatedAt, then lastUpdatedAt, createdAt, timestamp.
  const byCreated = pickNewestCodexThreadId(
    {
      threads: [
        { id: "older", cwd: "C:/work/repo", createdAt: 1 },
        { id: "newer", cwd: "C:/work/repo", createdAt: 5 },
      ],
    },
    "C:/work/repo",
  );
  assert.equal(byCreated, "newer", "createdAt orders threads that carry no updatedAt");
}

console.log("codex-thread-selection.test.js: all assertions passed");
