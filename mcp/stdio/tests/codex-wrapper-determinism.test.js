#!/usr/bin/env node
// Wrapper-render guards for the codex-aify bash wrapper (FIX 7, 2026-06-03 —
// codex bypass-flag determinism guard).
//
// Mirrors hermes-wrapper-determinism.test.js: renders the REAL bash wrapper via
// `install.sh --client codex --emit-codex-wrappers <dir>` (a test hook that emits
// ONLY the codex-aify wrapper, touching nothing else and launching no npm/codex),
// then:
//   1. `bash -n` the emitted body — a syntax error in the heredoc would otherwise
//      only surface at operator launch time. Regression guard for the heredoc.
//   2. grep the rendered text for the bypass invariant this guard locks in, so the
//      codex auto-approval bypass can't silently drop:
//        - CODEX_AUTO defaults true and adds --dangerously-bypass-approvals-and-sandbox
//          to CODEX_PERMISSION_FLAGS.
//        - both the app-server launch AND the foreground/resume TUI launch apply
//          ${CODEX_PERMISSION_FLAGS[@]} (so the bypass reaches every codex invocation).
//        - --safe / --no-auto opt out by setting CODEX_AUTO=false.

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

// Render the bash codex-aify wrapper into a throwaway dir and return its text.
function renderCodexWrapper() {
  const dir = tmpDir("aify-codex-wrapper-test-");
  try {
    execFileSync(
      "bash",
      [INSTALL_SH, "--client", "codex", "--emit-codex-wrappers", dir],
      { stdio: "ignore" },
    );
    const wrapperPath = path.join(dir, "codex-aify");
    assert.ok(fs.existsSync(wrapperPath), "install.sh --emit-codex-wrappers must emit codex-aify");
    return { text: fs.readFileSync(wrapperPath, "utf8"), wrapperPath, dir };
  } catch (err) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
    throw err;
  }
}

test("codex-aify wrapper: rendered heredoc body is syntactically valid (bash -n)", () => {
  const { wrapperPath, dir } = renderCodexWrapper();
  try {
    const res = spawnSync("bash", ["-n", wrapperPath], { encoding: "utf8" });
    assert.equal(res.status, 0, `bash -n failed:\n${res.stderr || res.stdout}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("codex-aify wrapper: CODEX_AUTO defaults true and adds --dangerously-bypass-approvals-and-sandbox", () => {
  const { text, dir } = renderCodexWrapper();
  try {
    assert.ok(/\bCODEX_AUTO=true\b/.test(text), "CODEX_AUTO must default to true (bypass on by default)");
    // A true CODEX_AUTO must populate the permission-flags array with the bypass flag.
    assert.ok(
      /if \[ "\$CODEX_AUTO" = true \]; then\s*\n\s*CODEX_PERMISSION_FLAGS\+=\(--dangerously-bypass-approvals-and-sandbox\)/.test(text),
      "a true CODEX_AUTO must add --dangerously-bypass-approvals-and-sandbox to CODEX_PERMISSION_FLAGS",
    );
    // Opt-out path present and flips the flag off.
    assert.ok(/"--safe"/.test(text) && /"--no-auto"/.test(text), "must honor --safe / --no-auto opt-out");
    assert.ok(/CODEX_AUTO=false/.test(text), "the opt-out branch must set CODEX_AUTO=false");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("codex-aify wrapper: bypass flags reach BOTH the app-server line and the foreground/resume TUI launch", () => {
  const { text, dir } = renderCodexWrapper();
  try {
    // The app-server launch applies the permission-flags array (both setsid and the
    // no-setsid fallback path).
    assert.ok(
      /codex "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" app-server --listen "\$APP_SERVER_URL"/.test(text),
      "the app-server launch must apply ${CODEX_PERMISSION_FLAGS[@]}",
    );
    // The foreground (fresh) TUI launch applies the permission-flags array.
    assert.ok(
      /run_codex_foreground --remote "\$APP_SERVER_URL" "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" "\$\{CODEX_ARGS\[@\]\}"\n/.test(text),
      "the foreground TUI launch must apply ${CODEX_PERMISSION_FLAGS[@]}",
    );
    // The resume TUI launch applies the permission-flags array too.
    assert.ok(
      /run_codex_foreground --remote "\$APP_SERVER_URL" "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" "\$\{CODEX_ARGS\[@\]\}" resume/.test(text),
      "the resume TUI launch must apply ${CODEX_PERMISSION_FLAGS[@]}",
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("codex-aify wrapper: managed sessions disable the built-in codex_apps MCP", () => {
  const { text, dir } = renderCodexWrapper();
  try {
    const managed = text.indexOf('if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]; then');
    const disableApps = text.indexOf("CODEX_PERMISSION_FLAGS+=(--disable apps)", managed);
    const appServer = text.indexOf('app-server --listen "$APP_SERVER_URL"', managed);
    assert.ok(managed >= 0 && disableApps > managed && appServer > disableApps,
      "managed wrappers must disable codex_apps before starting their app-server");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// IDENTITY RECOVERY on a hand-typed `codex-aify --resume <id>` (2026-07-28).
//
// Every turn-state path is gated on AIFY_AGENT_ID; with no id the agent's status LATCHES and
// nothing alive can clear it. claude-aify has recovered the agent from a bare --resume since
// 2026-07-14 and hermes-aify since 2026-06-03, but hermes-aify's own comment admitted codex had no
// such recovery — the dashboard's Resume passes --aify-agent, so only the HAND-TYPED operator path
// was broken, which is exactly the path an operator uses. Pinned here because the failure is silent:
// the wrapper still launches, the session still works, and only the fleet's status view rots.
test("codex-aify recovers the agent id from a bare --resume via the service", () => {
  const { text } = renderCodexWrapper();
  // Substring, not regex: the guard is an exact shell expression, and a regex here would only add
  // escaping bugs of its own.
  assert.ok(
    text.includes('[ -z "$CODEX_AIFY_AGENT_ID" ] && [ -n "${CODEX_RESUME_HANDLE:-}" ]'),
    "recovery must be gated on an EMPTY agent id plus a resume handle",
  );
  assert.match(text, /\/api\/v1\/agents/, "must ask the service which agent owns the handle");
  assert.match(text, /=== *"codex"/,
    'must scope the match to runtime "codex" so a claude agent sharing a handle cannot cross-bind');
  assert.match(text, /resolved aify agent/, "must announce a successful recovery");
  assert.match(text, /NO AGENT ID for --resume/,
    "must say so OUT LOUD when the id is still unknown — silent degradation is the whole defect");
});

test("codex-aify's recovery URL is substituted at install time, not left as a placeholder", () => {
  const { text } = renderCodexWrapper();
  assert.doesNotMatch(text, /__AIFY_INSTALL_TIME_URL__/,
    "an unsubstituted placeholder would make the lookup curl a literal string and always fail");
});
