#!/usr/bin/env node
// `definitions-fresh`: the doctor says when a host has stopped pushing its agent definitions (P0 C11, arm 3).
// The service decides staleness and writes the sentence; the row prints it, and keeps three answers apart:
// an unread service (unknown), a service that predates the field (skipped), and no host pushing (ok).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { definitionsFreshVerdict } from "../definitions-fresh-check.mjs";

const NOTICE = "definitions from win32:box not refreshed since 2026-10-02T10:00:00Z";
const pushing = (notice = "") => ({ id: "e1", definitions: { machineId: "win32:box", notice } });

test("A HOST THAT STOPPED PUSHING fails the row, in the service's words", () => {
  const verdict = definitionsFreshVerdict({ environments: [pushing(NOTICE), pushing("")] });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "not-refreshed");
  assert.ok(verdict.detail.startsWith(NOTICE), verdict.detail);
  assert.match(verdict.fix, /aify-env doctor/);
});

test("CONTROL: every host pushing passes, and no host pushing passes as none", () => {
  assert.deepEqual([definitionsFreshVerdict({ environments: [pushing("")] }).code,
    definitionsFreshVerdict({ environments: [{ id: "e", definitions: null }] }).code], ["fresh", "none"]);
  assert.equal(definitionsFreshVerdict({ environments: [pushing("")] }).ok, true);
});

test("AN UNREAD SERVICE is unknown, never a pass", () => {
  for (const environments of [null, undefined]) {
    const verdict = definitionsFreshVerdict({ environments });
    assert.deepEqual([verdict.ok, verdict.code], [false, "unknown-service"]);
  }
});

test("A SERVICE OLDER THAN THE FIELD is skipped, not read as no host pushing", () => {
  const verdict = definitionsFreshVerdict({ environments: [{ id: "e1" }, { id: "e2" }] });
  assert.equal(verdict.skipped, true);
  assert.match(verdict.detail, /predates/);
});

test("THE CALL SITE passes the listing with its null intact", () => {
  // `(await get(...))?.environments || []` turned an unread service into an empty fleet three times in
  // this repo (a-doctor-row-never-passes-on-a-service-it-did-not-reach.test.js). The doctor cannot be run
  // from a test, so its one call is read: it must take `rows`, which keeps the null.
  const doctor = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "doctor.js"), "utf8");
  const calls = [...doctor.matchAll(/definitionsFreshVerdict\(\{ environments: (\w+) \}\)/g)];
  assert.equal(calls.length, 1, "exactly one call site");
  assert.equal(calls[0][1], "rows");
  assert.match(doctor, /const rows = envListing \? \(envListing\.environments \|\| \[\]\) : null;/,
    "`rows` is the listing that keeps an unread service null");
});
