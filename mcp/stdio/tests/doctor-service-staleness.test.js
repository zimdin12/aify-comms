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
  SERVICE_IMAGE_PATHS,
  bridgeInstallVerdict,
  serviceBuildVerdict,
} from "../doctor-predicates.js";

const BUILT = "76fb7b9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const HEAD = "f94b884bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

// ── the false red this exists to prevent ─────────────────────────────────────────────
test("commits that touch NO image content are not stale", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, builtShort: "76fb7b9", headSha: HEAD, headShort: "f94b884",
    imageCommits: 0, totalCommits: 3,
  });
  assert.equal(v.ok, true, "a docs-only commit must not fail --strict");
  assert.equal(v.code, "ok");
  assert.match(v.detail, /none touching image content/);
  assert.match(v.detail, /3 commit\(s\) ahead/, "still reports the gap honestly");
  assert.equal(v.fix, "", "nothing for the operator to do");
});

test("the retired wording never comes back", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, headSha: HEAD, headShort: "f94b884", imageCommits: 0, totalCommits: 3,
  });
  assert.doesNotMatch(`${v.detail} ${v.fix}`, /changes are NOT running/i);
});

// ── genuine staleness must still fail ────────────────────────────────────────────────
test("commits that DO touch image content are stale", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, builtShort: "76fb7b9", headSha: HEAD, headShort: "f94b884",
    imageCommits: 2, totalCommits: 5,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale");
  assert.match(v.detail, /2 commit\(s\) since then changed code the image contains/);
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
    builtSha: BUILT, headSha: HEAD, headShort: "f94b884", imageCommits: 0, totalCommits: 3,
  });
  const br = bridgeInstallVerdict({
    installedSha: BUILT, headSha: HEAD, headShort: "f94b884", bridgeCommits: 0, totalCommits: 3,
  });
  assert.equal(svc.ok, br.ok, "both must be green when nothing relevant changed");
  assert.equal(svc.fix, br.fix, "neither should hand the operator a chore");
});

// ── the path list must match what the image actually contains ────────────────────────
test("SERVICE_IMAGE_PATHS covers every Dockerfile COPY source", () => {
  const dockerfile = readFileSync(new URL("../../../Dockerfile", import.meta.url), "utf8");
  const copied = [];
  for (const line of dockerfile.split("\n")) {
    const m = line.match(/^\s*COPY\s+(.+)$/);
    if (!m) continue;
    const parts = m[1].trim().split(/\s+/);
    parts.pop(); // destination
    for (const src of parts) {
      if (src.startsWith("--")) continue;
      copied.push(src.replace(/\/$/, ""));
    }
  }
  assert.ok(copied.length > 0, "Dockerfile must have COPY lines to check");
  for (const src of copied) {
    const top = src.split("/")[0];
    assert.ok(
      SERVICE_IMAGE_PATHS.includes(top) || SERVICE_IMAGE_PATHS.includes(src),
      `Dockerfile COPYs ${src} but SERVICE_IMAGE_PATHS does not cover "${top}" — `
        + "a change there would rebuild the image while doctor reported clean",
    );
  }
});

console.log("doctor-service-staleness.test.js: all assertions passed");
