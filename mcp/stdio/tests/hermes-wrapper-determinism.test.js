#!/usr/bin/env node
// Wrapper-render guards for the hermes-aify bash wrapper (Task 2.1 gateway-attach
// determinism + Task 4 --yolo bypass default).
//
// These render the REAL bash wrapper via `install.sh --emit-hermes-wrappers <dir>`
// (the test hook that emits ONLY the wrappers, touching nothing else and launching
// no npm/hermes/loop), then:
//   1. `bash -n` the emitted body — a syntax error in the heredoc (e.g. an
//      unescaped backtick / `$()` in a comment) would otherwise only surface at
//      operator launch time. This is the regression guard for the heredoc.
//   2. grep the rendered text for the two invariants this change locks in:
//        - Task 2.1: the inherited stale HERMES_TUI_GATEWAY_URL / AIFY_HERMES_GATEWAY_URL
//          are UNSET up front (so the visible TUI never attaches to a dead/foreign
//          gateway), AND the GATEWAY-HOST branch re-exports a FRESH HERMES_TUI_GATEWAY_URL
//          right before its exec (so the TUI attaches to THIS loop's gateway host).
//        - Task 4: HERMES_AUTO defaults true → the interactive `hermes --tui` gets
//          `--yolo`, opt-out via --safe/--no-auto.
//
// The .ps1 wrapper is only emitted on a Windows-style wrapper dir, so it isn't
// rendered here; its invariants are pinned by the Python install tests.

import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { tmpDir } from "./_tmpdir.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const INSTALL_SH = path.join(REPO, "install.sh");

// Render the bash hermes-aify wrapper into a throwaway dir and return its text.
function renderHermesWrapper() {
  const dir = tmpDir("aify-hermes-wrapper-test-");
  try {
    execFileSync(
      "bash",
      [INSTALL_SH, "--client", "hermes", "--emit-hermes-wrappers", dir],
      { stdio: "ignore" },
    );
    const wrapperPath = path.join(dir, "hermes-aify");
    assert.ok(fs.existsSync(wrapperPath), "install.sh --emit-hermes-wrappers must emit hermes-aify");
    return { text: fs.readFileSync(wrapperPath, "utf8"), wrapperPath, dir };
  } catch (err) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
    throw err;
  }
}

test("hermes-aify wrapper: rendered heredoc body is syntactically valid (bash -n)", () => {
  const { wrapperPath, dir } = renderHermesWrapper();
  try {
    const res = spawnSync("bash", ["-n", wrapperPath], { encoding: "utf8" });
    assert.equal(res.status, 0, `bash -n failed:\n${res.stderr || res.stdout}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("hermes-aify wrapper: Task 2.1 — clears inherited stale gateway env, then the gateway branch re-exports a FRESH HERMES_TUI_GATEWAY_URL before exec", () => {
  const { text, dir } = renderHermesWrapper();
  try {
    // Inherited stale gateway env is unset up front (so a fallback exec can never
    // attach to a dead/foreign gateway; the TUI only ever attaches to a fresh one).
    const unsetIdx = text.indexOf(
      "unset HERMES_TUI_GATEWAY_URL AIFY_HERMES_GATEWAY_URL AIFY_HERMES_GATEWAY_TOKEN",
    );
    assert.ok(unsetIdx > 0, "wrapper must unset inherited HERMES_TUI_GATEWAY_URL / AIFY_HERMES_GATEWAY_URL up front");
    // The GATEWAY-HOST branch re-exports the FRESH gateway URL the TUI attaches to.
    const exportIdx = text.indexOf('export HERMES_TUI_GATEWAY_URL="$HERMES_TUI_WS_URL"');
    assert.ok(exportIdx > 0, "gateway branch must export HERMES_TUI_GATEWAY_URL=<this loop's gateway wsUrl>");
    assert.ok(
      text.indexOf('export AIFY_HERMES_GATEWAY_URL="$HERMES_TUI_WS_URL"') > 0,
      "gateway branch must also export AIFY_HERMES_GATEWAY_URL so the MCP child registers a real ws:// gateway",
    );
    // Order matters: the up-front unset must precede the gateway-branch fresh
    // export, so the fresh value always wins on the attach path.
    assert.ok(unsetIdx < exportIdx, "the up-front unset must come BEFORE the gateway branch's fresh export");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("hermes-aify wrapper: Task 4 — HERMES_AUTO defaults true and the interactive TUI gets --yolo (opt-out via --safe/--no-auto)", () => {
  const { text, dir } = renderHermesWrapper();
  try {
    assert.ok(/\bHERMES_AUTO=true\b/.test(text), "HERMES_AUTO must default to true (bypass on by default)");
    // The bypass flag array is populated with --yolo when HERMES_AUTO is true.
    assert.ok(
      /if \[ "\$HERMES_AUTO" = true \]; then\s*\n\s*HERMES_PERMISSION_FLAGS\+=\(--yolo\)/.test(text),
      "a true HERMES_AUTO must add --yolo to HERMES_PERMISSION_FLAGS",
    );
    // Opt-out path present.
    assert.ok(/"--safe"/.test(text) && /"--no-auto"/.test(text), "must honor --safe / --no-auto opt-out");
    assert.ok(/HERMES_AUTO=false/.test(text), "the opt-out branch must set HERMES_AUTO=false");
    // The interactive --tui exec(s) apply the permission flags.
    assert.ok(
      /--tui "\$\{HERMES_PERMISSION_FLAGS\[@\]\}"/.test(text),
      "the interactive `hermes --tui` exec must apply HERMES_PERMISSION_FLAGS (the --yolo default)",
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ── The harness wrapper contract (v0.6 Phase 2) ─────────────────────────────
//
// TEXT, NOT EXECUTION, and that is a deliberate exception rather than an oversight. claude, codex and
// pi are each guarded by a harness that RUNS the wrapper against a stub, because text cannot tell you
// a flag reached the command line. Hermes is not, because running it is unsafe on a machine with a
// live fleet: before it execs anything the wrapper reaps by agent id, and one of those reaps is
//
//     lsof -ti tcp:$(agentPort "$agent_id") | xargs kill
//
// — a port derived from the id. A test agent id colliding with a live agent's port would kill the
// operator's gateway host. This repo already has that incident recorded once, from a test that
// acquired a role it should not have had.
//
// So the assertions below cover the SHAPE of the contract, and `--check` is the one path that could
// be executed safely later (it exits above every reap and every spawn) if this is ever revisited in a
// window where nothing is running.

test("hermes-aify wrapper: the contract is resolved before anything is started", () => {
  const { text, dir } = renderHermesWrapper();
  try {
    const contractAt = text.indexOf("HARNESS_ENDPOINT=");
    assert.ok(contractAt >= 0, "the wrapper must resolve HARNESS_ENDPOINT");

    // Everything this wrapper does that touches the machine must come AFTER the contract block.
    for (const marker of ["aify_hermes_kill_prior", "ensure-host", "nohup"]) {
      const at = text.indexOf(marker);
      if (at < 0) continue;
      assert.ok(
        at > contractAt,
        `${marker} appears before the contract is resolved — a rejected configuration would already `
          + "have changed the machine",
      );
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("hermes-aify wrapper: contract inputs fall back to the legacy AIFY_* names", () => {
  // The whole reason a live fleet survives this change: with no HARNESS_* set, every input resolves
  // to exactly what the wrapper used before the contract existed.
  const { text, dir } = renderHermesWrapper();
  try {
    assert.match(text, /HARNESS_IDENTITY="\$\{HARNESS_IDENTITY:-\$\{AIFY_AGENT_ID:-\}\}"/);
    assert.match(text, /HARNESS_ROLE="\$\{HARNESS_ROLE:-\$\{AIFY_AGENT_ROLE:-\}\}"/);
    assert.match(text, /HERMES_AIFY_AGENT_ID="\$HARNESS_IDENTITY"/, "identity must flow from the contract");
    assert.match(text, /export AIFY_SERVER_URL="\$HARNESS_ENDPOINT"/, "and so must the endpoint");
    // `-` not `:-` on the endpoint: an explicitly emptied value is a configuration error, not unset.
    assert.doesNotMatch(text, /HARNESS_ENDPOINT="\$\{HARNESS_ENDPOINT:-/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("hermes-aify wrapper: --check reports and exits before any side effect", () => {
  const { text, dir } = renderHermesWrapper();
  try {
    const checkAt = text.indexOf('"--check"');
    assert.ok(checkAt >= 0, "--check must be handled");
    const exitAt = text.indexOf("OK — nothing was started.", checkAt);
    assert.ok(exitAt > checkAt, "--check must report that it started nothing");
    const killAt = text.indexOf("aify_hermes_kill_prior");
    if (killAt >= 0) {
      assert.ok(exitAt < killAt, "--check must exit above the process reap");
    }
    assert.match(text, /exit "\$HARNESS_EXIT_CONFIG"/, "an invalid configuration must exit 78");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("hermes-aify wrapper: reports its own version for doctor's wrapper-current check", () => {
  const { text, dir } = renderHermesWrapper();
  try {
    assert.match(
      text,
      /HARNESS_WRAPPER_VERSION="\d+\.\d+\.\d+"/,
      "a literal version, substituted at render — not a command the wrapper runs at launch",
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
