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
import { runWrapper } from "./wrapper-harness.mjs";

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

// ── Behaviour and the harness contract ──────────────────────────────────────
//
// Pi's resident wrapper is not installed, so nothing else would ever execute this file. That makes
// running it here the ONLY evidence it works at all — a template nobody renders and nobody runs is
// indistinguishable from a broken one.

const piWrapper = () => path.join(renderPiWrappers().dir, "pi-aify");
const run = (opts = {}) => runWrapper(piWrapper(), { runtimeName: "omp", ...opts });

test("pi-aify launches its runtime and forwards argv", () => {
  const r = run({ args: ["--print", "hello world"] });
  assert.equal(r.launched, true, `wrapper never reached omp:\n${r.stderr}`);
  assert.ok(r.argv.includes("--print"));
  assert.ok(r.argv.includes("hello world"), "an argument with a space must survive as ONE entry");
  assert.ok(r.argv.includes("--auto-approve"), "the unattended bypass must be on by default");
});

test("pi-aify exports the identity the bridge registers under", () => {
  const r = run({ args: ["--aify-agent", "probe-agent", "--aify-role", "tester"] });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, "probe-agent");
  assert.equal(r.env.AIFY_AGENT_ROLE, "tester");
  assert.equal(r.env.AIFY_RUNTIME, "pi");
});

test("HARNESS_IDENTITY and HARNESS_ENDPOINT drive the wrapper, flag still winning", () => {
  const r = run({ env: { HARNESS_IDENTITY: "harness-agent", HARNESS_ENDPOINT: "http://127.0.0.2:2/x" } });
  assert.equal(r.env.AIFY_AGENT_ID, "harness-agent");
  assert.equal(r.env.AIFY_COMMS_URL, "http://127.0.0.2:2/x");

  const flag = run({ args: ["--aify-agent", "flag-agent"], env: { HARNESS_IDENTITY: "harness-agent" } });
  assert.equal(flag.env.AIFY_AGENT_ID, "flag-agent");
});

test("--check reports the resolved configuration and starts nothing", () => {
  const r = run({ args: ["--check"], env: { HARNESS_IDENTITY: "probe-agent" } });
  assert.equal(r.launched, false, "--check must not launch the runtime");
  assert.equal(r.status, 0);
  const out = `${r.stdout}${r.stderr}`;
  assert.match(out, /pi-aify \d+\.\d+\.\d+/, "it must report its own version");
  assert.match(out, /probe-agent/, "and the identity it resolved");
  assert.match(out, /127\.0\.0\.2:1/, "and the endpoint");
});

test("an explicitly empty HARNESS_ENDPOINT exits 78 without starting anything", () => {
  const r = run({ env: { HARNESS_ENDPOINT: "" } });
  assert.equal(r.launched, false);
  assert.equal(r.status, 78, `expected 78, got ${r.status}: ${r.stderr}`);
});

test("HARNESS_EXTRA_ENV exports host-supplied pairs verbatim", () => {
  const r = run({ env: { HARNESS_EXTRA_ENV: "FOO=bar\nnot-a-pair\n" } });
  assert.equal(r.launched, true, `malformed entries must not be fatal: ${r.stderr}`);
  assert.equal(r.env.FOO, "bar");
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
