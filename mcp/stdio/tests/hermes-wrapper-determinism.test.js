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
