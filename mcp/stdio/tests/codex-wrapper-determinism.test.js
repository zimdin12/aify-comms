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
//   2. grep the rendered text for what running the wrapper cannot see: both the
//      app-server launch AND the foreground/resume TUI launch apply
//      ${CODEX_PERMISSION_FLAGS[@]} (the stub records only the foreground launch).
//      The bypass being on by default and --safe removing it is proven by running
//      the wrapper in codex-wrapper-behaviour.test.js.

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

// RENDERED ONCE FOR THE TEXT ASSERTIONS.
//
// Every test below that only READS the rendered text was paying its own full install.sh run.
// Measured 2026-08-29 across the bridge suite, the four `*-wrapper-determinism` files held 209.7s of a
// 551s run -- pi 89.9s, hermes 61.5s, codex 35.7s, claude 22.6s -- and their assertions are regex
// matches over one artifact. The render is the FIXTURE here, not the subject: rendering it once per
// case proves the same thing N-1 extra times.
//
// The bash -n case reads the same render: it only reads the file, so sharing cannot let one case
// see another's work. tmpDir removes the directory at exit. (It rendered privately until
// 2026-09-18, a second full install.sh run for the same artifact.)
let sharedRender = null;
function shared() {
  if (!sharedRender) sharedRender = renderCodexWrapper();
  return sharedRender;
}
const sharedText = () => shared().text;

test("codex-aify wrapper: rendered heredoc body is syntactically valid (bash -n)", () => {
  const res = spawnSync("bash", ["-n", shared().wrapperPath], { encoding: "utf8" });
  assert.equal(res.status, 0, `bash -n failed:\n${res.stderr || res.stdout}`);
});

test("codex-aify wrapper: bypass flags reach BOTH the app-server line and the foreground/resume TUI launch", () => {
  const text = sharedText();
  // The app-server launch applies the permission-flags array (both setsid and the
  // no-setsid fallback path).
  assert.ok(
    /codex "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" (?:"\$\{CODEX_HERDR_HOOKS\[@\]\}" )?app-server --listen "\$APP_SERVER_URL"/.test(text),
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
});

test("codex-aify wrapper: managed sessions disable the built-in codex_apps MCP", () => {
  const text = sharedText();
  const managed = text.indexOf('if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]; then');
  const disableApps = text.indexOf("CODEX_PERMISSION_FLAGS+=(--disable apps)", managed);
  const appServer = text.indexOf('app-server --listen "$APP_SERVER_URL"', managed);
  assert.ok(managed >= 0 && disableApps > managed && appServer > disableApps,
    "managed wrappers must disable codex_apps before starting their app-server");
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
  const text = sharedText();
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
  const text = sharedText();
  assert.doesNotMatch(text, /__AIFY_INSTALL_TIME_URL__/,
    "an unsubstituted placeholder would make the lookup curl a literal string and always fail");
});
