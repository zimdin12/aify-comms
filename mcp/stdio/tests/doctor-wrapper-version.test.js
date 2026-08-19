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

import { wrapperVersionVerdict } from "../doctor-predicates.js";

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

test("an unknown repo version cannot be compared, and says so", () => {
  const v = wrapperVersionVerdict({ repoVersion: "", wrappers: [{ name: "claude-aify", version: V }] });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-all");
  assert.match(v.detail, /repo version/i);
});

test("the verdict reports how many were checked, so a shrinking scan is visible", () => {
  // A check that silently examined one wrapper reads exactly like one that examined four.
  const v = wrapperVersionVerdict({
    repoVersion: V,
    wrappers: [{ name: "claude-aify", version: V }, { name: "codex-aify", version: V }],
  });
  assert.match(v.detail, /2/, "the count of wrappers examined must appear");
});
