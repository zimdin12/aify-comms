#!/usr/bin/env node
// `wrapper-current` — the check that replaces a guarantee v0.6 gives up.
//
// install.sh currently guarantees the wrapper and the bridge are the same build, by generating one
// and copying the other in a single step. Publishing the wrappers as an npm package (operator
// decision, 2026-08-19) breaks that guarantee by construction: the two version separately from then
// on. `bridge-installed` and `bridge-current` exist BECAUSE that guarantee kept being violated
// silently, so trading it away without a replacement would hand back the exact class of failure this
// tool was built for — a wrapper executing one build while another sits on disk.
//
// This is the replacement, and it reads rather than runs. Executing an installed wrapper to ask its
// version is not safe here: a wrapper installed before the contract existed does not KNOW `--check`,
// and would forward it to the runtime as an unrecognised flag — i.e. doctor would LAUNCH CLAUDE on a
// machine with a live fleet in order to find out whether claude-aify was current. Reading the marker
// out of the file answers the same question and cannot start anything.

import assert from "node:assert/strict";
import { test } from "node:test";

import { versionToCompareWrappersAgainst, wrapperVersionVerdict } from "../doctor-predicates.js";

const V = "0.5.7";

test("every installed wrapper matching the repo version is ok", () => {
  const v = wrapperVersionVerdict({
    repoVersion: V,
    wrappers: [
      { name: "claude-aify", version: V },
      { name: "codex-aify", version: V },
    ],
  });
  assert.equal(v.ok, true);
  assert.equal(v.code, "ok");
  assert.match(v.detail, /claude-aify/);
});

test("a wrapper on an older version is reported stale, and named", () => {
  const v = wrapperVersionVerdict({
    repoVersion: V,
    wrappers: [
      { name: "claude-aify", version: V },
      { name: "codex-aify", version: "0.5.4" },
    ],
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale");
  assert.match(v.detail, /codex-aify/, "the stale one must be named");
  assert.match(v.detail, /0\.5\.4/, "with the version it is actually on");
  assert.doesNotMatch(v.detail, /claude-aify/, "and the current one must not be blamed");
  assert.match(v.fix, /install\.sh/, "the fix is a reinstall, not a restart");
});

test("a wrapper with no version marker predates the contract and counts as stale", () => {
  // Not an error and not a pass. Every wrapper installed before v0.6 carries no marker, so this is
  // the state an operator upgrading INTO this check is in — it must read as "reinstall", not as a
  // malfunction, and not as green.
  const v = wrapperVersionVerdict({
    repoVersion: V,
    wrappers: [{ name: "claude-aify", version: null }],
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale");
  assert.match(v.detail, /claude-aify/);
  assert.match(v.detail, /pre-contract|no version marker/i, "and must say why it could not be read");
});

test("no readable wrapper is unknown-all, never ok", () => {
  // The repo's standing rule, learned twice from this very tool: a check that gathered no evidence
  // must not report ok. `env-bridge` counted registered rows and said "2 connected" with zero bridges
  // alive; `bridge-current` was green-by-default when no bridge reported a build.
  const v = wrapperVersionVerdict({ repoVersion: V, wrappers: [] });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-all");
  assert.match(v.detail, /no .*wrapper/i);
});

test("an unknown wrapper-package version cannot be compared, and says so", () => {
  const v = wrapperVersionVerdict({ repoVersion: "", wrappers: [{ name: "claude-aify", version: V }] });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-all");
  // It must name the package it could not read. Saying "repo version" sent a reader to
  // aify-comms' VERSION file, which is the wrong number and was the bug.
  assert.match(v.detail, /aify-wrapper package version/i);
  assert.match(v.fix, /npm install/i);
});

test("the verdict reports how many were checked, so a shrinking scan is visible", () => {
  // A check that silently examined one wrapper reads exactly like one that examined four.
  const v = wrapperVersionVerdict({
    repoVersion: V,
    wrappers: [{ name: "claude-aify", version: V }, { name: "codex-aify", version: V }],
  });
  assert.match(v.detail, /2/, "the count of wrappers examined must appear");
});

// ── Which version a launcher is compared AGAINST ─────────────────────────────────────
//
// A launcher's HARNESS_WRAPPER_VERSION marker is stamped from aify-wrapper's VERSION at render time.
// `wrapper-current` was comparing it to aify-comms' repo-root VERSION -- a different counter, kept in
// a different repo, released on a different schedule. They both read 0.5.7 on 2026-08-20, so the check
// was green by coincidence rather than by construction.
//
// The moment either repo cuts a release the other does not, every host reports `wrapper-current`
// STALE, immediately after a clean install, forever. Separate release lines are the entire point of
// the three-repo split, so that is not a risk -- it is a date.
test("a launcher is compared against the package that stamped it, never against aify-comms", () => {
  assert.equal(
    versionToCompareWrappersAgainst({ wrapperPackageVersion: "0.7.0", serviceVersion: "0.6.0" }),
    "0.7.0",
  );
  // The coincidence that hid this: both counters agreeing says nothing about which one is right.
  assert.equal(
    versionToCompareWrappersAgainst({ wrapperPackageVersion: "0.5.7", serviceVersion: "0.5.7" }),
    "0.5.7",
  );
});

test("no readable wrapper package means nothing to compare, not aify-comms' number", () => {
  // Falling back to the service version is what produced the bug, so absence must stay absent.
  // wrapperVersionVerdict turns "" into unknown-all, which is a FAIL that says it verified nothing.
  for (const absent of ["", "   ", undefined, null]) {
    assert.equal(
      versionToCompareWrappersAgainst({ wrapperPackageVersion: absent, serviceVersion: "0.6.0" }),
      "",
      `a ${JSON.stringify(absent)} package version must not borrow the service's`,
    );
  }
  const verdict = wrapperVersionVerdict({
    repoVersion: versionToCompareWrappersAgainst({ wrapperPackageVersion: "", serviceVersion: "0.6.0" }),
    wrappers: [{ name: "claude-aify", version: "0.6.0" }],
  });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
});
