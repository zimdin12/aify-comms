#!/usr/bin/env node
// Render guard for the pi-aify / omp-aify bash wrappers (v0.6 Phase 2).
//
// Pi is in v0.6's wrapper scope (operator, 2026-08-19: claude + codex + pi, hermes last), so its
// wrapper needs the same net the others get before it is parameterised onto the HARNESS_* contract.
//
// INSTALLING pi is disabled and stays disabled — OMP is single-client, so `omp-aify` cannot provide
// resident wake into an open TUI. RENDERING it is a different act, and `--emit-pi-wrappers` is carved
// out of the disable check for exactly that reason. If that carve-out is ever removed the emit hook
// becomes unreachable, so the first test here asserts the hook actually runs rather than assuming it.

import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { tmpDir } from "./_tmpdir.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const INSTALL_SH = path.join(REPO, "install.sh");

function renderPiWrappers() {
  const dir = tmpDir("aify-pi-wrapper-test-");
  try {
    execFileSync(
      "bash",
      [INSTALL_SH, "--client", "pi", "http://127.0.0.1:8899", "--emit-pi-wrappers", dir],
      { stdio: "ignore" },
    );
    return { dir, files: fs.readdirSync(dir) };
  } catch (err) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
    throw err;
  }
}

test("pi wrappers render despite pi INSTALLS being disabled", () => {
  const { dir, files } = renderPiWrappers();
  try {
    assert.ok(files.includes("pi-aify"), `expected pi-aify, got ${JSON.stringify(files)}`);
    assert.ok(files.includes("omp-aify"), "the omp-aify alias must render alongside it");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("pi wrappers: rendered bodies are syntactically valid (bash -n)", () => {
  const { dir } = renderPiWrappers();
  try {
    for (const name of ["pi-aify", "omp-aify"]) {
      const res = spawnSync("bash", ["-n", path.join(dir, name)], { encoding: "utf8" });
      assert.equal(res.status, 0, `bash -n ${name} failed:\n${res.stderr || res.stdout}`);
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("pi INSTALL is still refused without the emit flag", () => {
  // The carve-out must be exactly that. If `--client pi` alone ever started installing, this repo
  // would be shipping a resident wrapper for a single-client runtime that cannot wake.
  const res = spawnSync("bash", [INSTALL_SH, "--client", "pi", "http://127.0.0.1:8899"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  assert.equal(res.status, 1, "pi install must still exit 1");
  assert.match(`${res.stdout}${res.stderr}`, /disabled/i, "and must say why");
});
