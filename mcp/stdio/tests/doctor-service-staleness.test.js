#!/usr/bin/env node
// A docs-only commit must not report "your service changes are NOT running" (false red).
//
// Observed live 2026-08-10: three commits touching only `docs/` and `scripts/` produced
//   "serving build 76fb7b9, repo HEAD is f94b884 (3 commit(s) behind).
//    Your service changes are NOT running. Rebuild."
// There were no service changes. `--strict` exits 1 on that, so a documentation commit would
// fail any script or CI gate using it.
//
// N13 already taught this lesson for `bridge-installed` and the fix was never carried across.
// This pins the carry-across so the two checks cannot drift apart again.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  SERVICE_IMAGE_NON_RUNTIME_PATHS,
  SERVICE_RUNTIME_EXCLUDE_PATHS,
  SERVICE_RUNTIME_PATHS,
  bridgeInstallVerdict,
  serviceBuildVerdict,
} from "../doctor-predicates.js";

const BUILT = "76fb7b9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const HEAD = "f94b884bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

// ── the false red this exists to prevent ─────────────────────────────────────────────
test("commits that touch NO runtime content are not stale", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, builtShort: "76fb7b9", headSha: HEAD, headShort: "f94b884",
    runtimeCommits: 0, totalCommits: 3,
  });
  assert.equal(v.ok, true, "a docs-only commit must not fail --strict");
  assert.equal(v.code, "ok");
  assert.match(v.detail, /none touching code the service executes/);
  assert.match(v.detail, /3 commit\(s\) ahead/, "still reports the gap honestly");
  assert.equal(v.fix, "", "nothing for the operator to do");
});

test("the retired wording never comes back", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, headSha: HEAD, headShort: "f94b884", runtimeCommits: 0, totalCommits: 3,
  });
  assert.doesNotMatch(`${v.detail} ${v.fix}`, /changes are NOT running/i);
});

// ── genuine staleness must still fail ────────────────────────────────────────────────
test("commits that DO touch runtime content are stale", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, builtShort: "76fb7b9", headSha: HEAD, headShort: "f94b884",
    runtimeCommits: 2, totalCommits: 5,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale");
  assert.match(v.detail, /2 commit\(s\) since then changed code the service EXECUTES/);
  assert.match(v.detail, /RUNNING service is older/);
  assert.match(v.fix, /stamp\.sh.*docker compose up -d --build/s);
});

test("equal shas are ok", () => {
  const v = serviceBuildVerdict({ builtSha: BUILT, builtShort: "76fb7b9", headSha: BUILT });
  assert.equal(v.ok, true);
  assert.match(v.detail, /== repo HEAD/);
});

test("a service reporting no build sha is still a failure", () => {
  const v = serviceBuildVerdict({ builtSha: "", headSha: HEAD });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-build");
  assert.match(v.fix, /stamp\.sh/);
});

test("no checkout to compare against is not a failure", () => {
  const v = serviceBuildVerdict({ builtSha: BUILT, builtShort: "76fb7b9", headSha: "" });
  assert.equal(v.ok, true);
  assert.match(v.detail, /no checkout to compare against/);
});

// ── the two checks must keep agreeing in shape ───────────────────────────────────────
test("service and bridge verdicts behave identically on the benign case", () => {
  const svc = serviceBuildVerdict({
    builtSha: BUILT, headSha: HEAD, headShort: "f94b884", runtimeCommits: 0, totalCommits: 3,
  });
  const br = bridgeInstallVerdict({
    installedSha: BUILT, headSha: HEAD, headShort: "f94b884", bridgeCommits: 0, totalCommits: 3,
  });
  assert.equal(svc.ok, br.ok, "both must be green when nothing relevant changed");
  assert.equal(svc.fix, br.fix, "neither should hand the operator a chore");
});

// ── every Dockerfile COPY source must be ACCOUNTED FOR, runtime or not ───────────────
// Reviewer's design: narrowing to runtime paths must not lose drift visibility. So each COPY
// source is either runtime (a rebuild trigger) or explicitly listed as non-runtime cargo WITH A
// REASON. Silent omission is the failure mode to prevent — that would let a new COPY of genuinely
// executed code slip in while doctor reported clean, which is a false GREEN, the worse direction.
test("every Dockerfile COPY source is either runtime or reasoned non-runtime cargo", () => {
  const dockerfile = readFileSync(new URL("../../../Dockerfile", import.meta.url), "utf8");
  const copied = new Set();
  for (const line of dockerfile.split("\n")) {
    const m = line.match(/^\s*COPY\s+(.+)$/);
    if (!m) continue;
    const parts = m[1].trim().split(/\s+/);
    parts.pop(); // destination
    for (const src of parts) {
      if (src.startsWith("--")) continue;
      copied.add(src.replace(/\/$/, ""));
    }
  }
  assert.ok(copied.size > 0, "Dockerfile must have COPY lines to check");
  for (const src of copied) {
    const top = src.split("/")[0];
    const isRuntime = SERVICE_RUNTIME_PATHS.some((p) => p === src || p === top || p.startsWith(`${src}/`));
    const isCargo = Object.hasOwn(SERVICE_IMAGE_NON_RUNTIME_PATHS, src)
      || Object.hasOwn(SERVICE_IMAGE_NON_RUNTIME_PATHS, top);
    assert.ok(
      isRuntime || isCargo,
      `Dockerfile COPYs "${src}" but it is neither in SERVICE_RUNTIME_PATHS nor listed in `
        + "SERVICE_IMAGE_NON_RUNTIME_PATHS with a reason. Decide which it is — if the service "
        + "executes it, doctor must demand a rebuild; if not, say why not.",
    );
  }
});

test("every non-runtime entry carries a non-trivial reason", () => {
  for (const [path, reason] of Object.entries(SERVICE_IMAGE_NON_RUNTIME_PATHS)) {
    assert.equal(typeof reason, "string");
    assert.ok(reason.length > 25, `"${path}" needs a real reason, got: ${JSON.stringify(reason)}`);
  }
});

test("host-side bridge code is NOT a service rebuild trigger", () => {
  // The concrete false red that forced this rework: a bridge-only commit demanded a container
  // rebuild for code the container never executes.
  assert.ok(!SERVICE_RUNTIME_PATHS.includes("mcp"), "whole mcp/ must not be a runtime path");
  assert.ok(!SERVICE_RUNTIME_PATHS.includes("mcp/stdio"));
  assert.ok(SERVICE_RUNTIME_PATHS.includes("mcp/sse_server.py"),
    "but the SSE transport the service does load must be");
});

test("test files under a runtime path are excluded", () => {
  // Found by this very fix flagging its own commit: adding service/tests/... demanded a rebuild.
  // Nothing in the image runs pytest, so a test-only commit cannot change what the service does.
  assert.ok(SERVICE_RUNTIME_EXCLUDE_PATHS.includes("service/tests"));
  assert.ok(SERVICE_RUNTIME_EXCLUDE_PATHS.some((p) => p.endsWith("*.test.mjs")));
  // Excludes must sit INSIDE a runtime path, or they are pointless.
  for (const ex of SERVICE_RUNTIME_EXCLUDE_PATHS) {
    assert.ok(
      SERVICE_RUNTIME_PATHS.some((rp) => ex === rp || ex.startsWith(`${rp}/`)),
      `exclude "${ex}" is not inside any runtime path — it excludes nothing`,
    );
  }
});

console.log("doctor-service-staleness.test.js: all assertions passed");

// ── a DIFFERENT sha that is ZERO commits behind is a contradiction, not a pass ────────
//
// External review of the release ladder, 2026-08-11. `doctor.js` computes both commit counts with
// `git rev-list --count <builtSha>..HEAD`. When the built sha is not in local history — deployed
// from an unmerged branch, a force-push, another clone — that git call FAILS, `sh()` returns "",
// both counts arrive as 0, and this predicate fell through to "healthy — 0 commit(s) ahead".
//
// A false green for exactly the "serving ≠ HEAD" case the check exists to catch, inside the tool
// whose whole theme is refusing to pass on absent evidence. Same class as `bridge-current`'s
// unknown-all, one layer down and found by someone else.
test("an unrelated built sha with zero commits between is UNKNOWN, not healthy", () => {
  const v = serviceBuildVerdict({
    builtSha: "deadbeefdeadbeefdeadbeef",
    headSha: "cafebabecafebabecafebabe",
    headShort: "cafebab",
    runtimeCommits: 0,
    totalCommits: 0,
  });
  assert.equal(v.ok, false, "a contradiction must not read as a clean bill of health");
  assert.equal(v.code, "unknown-build");
  assert.match(v.detail, /cannot both be true/);
  assert.match(v.fix, /Fetch the branch|rebuild/i);
});

test("the same-sha case is still plainly healthy", () => {
  // The guard must not swallow the ordinary green: identical shas legitimately have zero commits
  // between them, and that is the most common state on a healthy host.
  const sha = "1234567890abcdef1234567890abcdef12345678";
  const v = serviceBuildVerdict({
    builtSha: sha, headSha: sha, headShort: "1234567", runtimeCommits: 0, totalCommits: 0,
  });
  assert.equal(v.ok, true, "build == HEAD is the normal healthy case and must stay green");
});

test("a real behind-by-docs-only state is still green, not unknown", () => {
  const v = serviceBuildVerdict({
    builtSha: "aaaaaaaaaaaa", headSha: "bbbbbbbbbbbb", headShort: "bbbbbbb",
    runtimeCommits: 0, totalCommits: 4,
  });
  assert.equal(v.ok, true, "behind by commits that cannot reach the container is not staleness");
  assert.equal(v.code, "ok");
});
