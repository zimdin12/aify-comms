#!/usr/bin/env node
// Wrapper-render guards for the claude-aify bash wrapper (v0.6 Phase 2, Task 2.2).
//
// WHY THIS FILE EXISTS. codex and hermes have had render tests since 2026-06-03: they emit the REAL
// wrapper into a throwaway dir, `bash -n` it, and assert on the rendered text. Claude — the runtime
// the whole fleet actually runs on — had none. Its coverage was three
// `service/tests/test_install_claude_*.py` files whose own docstring says "static-text smoke checks
// on install.sh — no bash exec": they grep the INSTALLER SOURCE for a substring. That proves a line
// was written, never that the emitted wrapper contains it, is reachable, or parses. A heredoc that
// mangles an escape renders a broken wrapper while every one of those assertions stays green.
//
// This matters now specifically because Phase 2 parameterises this wrapper onto the HARNESS_*
// contract. A refactor of generated shell with only source-regex tests behind it is a refactor with
// no net at all, so the net comes first and the parameterisation lands under it.
//
// SAFETY: `--emit-claude-wrappers <dir>` renders into the given dir and exits BEFORE npm, before MCP
// registration, before any hook install and before any env mutation. It cannot touch the operator's
// live `~/.local/bin/claude-aify` or `~/.aify-comms`, which matters because this suite runs against a
// machine with a working fleet on it.

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

// A URL is passed positionally so SERVER_URL is non-empty at render time. It is a literal, not the
// operator's configured endpoint: this suite must render the same text on every machine.
const RENDER_URL = "http://127.0.0.1:8899";

function renderClaudeWrapper() {
  const dir = tmpDir("aify-claude-wrapper-test-");
  try {
    execFileSync(
      "bash",
      [INSTALL_SH, "--client", "claude", RENDER_URL, "--emit-claude-wrappers", dir],
      { stdio: "ignore" },
    );
    const wrapperPath = path.join(dir, "claude-aify");
    assert.ok(fs.existsSync(wrapperPath), "install.sh --emit-claude-wrappers must emit claude-aify");
    return { text: fs.readFileSync(wrapperPath, "utf8"), wrapperPath, dir };
  } catch (err) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
    throw err;
  }
}

// RENDERED ONCE FOR THE TEXT ASSERTIONS.
//
// Every test below that only READS the rendered text was paying its own full install.sh run: nine
// renders, 65.4 seconds measured on 2026-08-29 for a file whose assertions are regex matches. The
// render is the fixture here, not the subject -- these cases each name a different property of one
// artifact, so rendering it nine times proves the same thing eight extra times.
//
// The two tests that inspect or delete the DIRECTORY keep their own private render, because they
// mutate it. A shared directory would make one of them delete the other's fixture.
let sharedRender = null;
function sharedText() {
  if (!sharedRender) sharedRender = renderClaudeWrapper();
  return sharedRender.text;
}

test("claude-aify wrapper: rendered heredoc body is syntactically valid (bash -n)", () => {
  const { wrapperPath, dir } = renderClaudeWrapper();
  try {
    const res = spawnSync("bash", ["-n", wrapperPath], { encoding: "utf8" });
    assert.equal(res.status, 0, `bash -n failed:\n${res.stderr || res.stdout}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("claude-aify wrapper: emitting touches nothing but the target dir", () => {
  // The emit hook's whole safety claim. If it ever stopped exiting early, this suite would start
  // reinstalling the operator's wrappers on every run — silently, and with a half-configured install.
  //
  // TWO LAUNCHERS SINCE 2026-08-29, not one. `aify-comms` — the environment-bridge launcher — joined
  // the render-only hook because it is the ONE launcher carrying the delegation setting and the one
  // no test could render: it wrote only to the operator's bin. A regression in exactly that setting
  // then reached a live host, turning managed spawns off aify-env on a routine re-install.
  //
  // The safety claim is UNCHANGED and this assertion still carries it. Emit mode's promise is that
  // it exits before anything mutates the environment, and a fall-through would write MCP config,
  // hooks and a native copy — all of which this exact-set comparison still catches. A launcher is
  // what emit mode is for; adding the one that was missing makes it more complete, not less safe.
  const { dir } = renderClaudeWrapper();
  try {
    assert.deepEqual(
      fs.readdirSync(dir).sort().filter((f) => !f.endsWith(".cmd")),
      ["aify-comms", "claude-aify"],
      "emit mode must produce the two launchers and nothing else",
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("claude-aify wrapper: exports the runtime identity the bridge registers under", () => {
  const text = sharedText();
  assert.match(text, /export AIFY_RUNTIME="claude-code"/, "AIFY_RUNTIME must be exported");
  assert.match(text, /export AIFY_AGENT_ID="\$CLAUDE_AIFY_AGENT_ID"/, "agent id must be exported");
  assert.match(text, /export AIFY_AGENT_ROLE="\$CLAUDE_AIFY_ROLE"/, "role must be exported");
  assert.match(text, /export AIFY_SESSION_MODE=/, "session mode must be exported");
});

test("claude-aify wrapper: the endpoint resolves through the contract, baked value last", () => {
  // The turn-end hook POSTs here, so an empty value silently costs every reply a 120s stale-window
  // wait rather than failing loudly. This asserts the RESOLUTION CHAIN is present in the text;
  // that the chain produces the right value under each combination of inputs is proven by running
  // the wrapper in claude-wrapper-behaviour.test.js and claude-wrapper-contract.test.js.
  const text = sharedText();
  assert.match(
    text,
    /HARNESS_ENDPOINT="\$\{HARNESS_ENDPOINT-\$\{AIFY_COMMS_URL:-http:\/\/127\.0\.0\.1:8899\}\}"/,
    "contract name first, legacy name second, the URL install.sh was given last",
  );
  assert.match(
    text,
    /export AIFY_COMMS_URL="\$HARNESS_ENDPOINT"/,
    "and the runtime must receive the RESOLVED endpoint, not re-derive it",
  );
  // `-` not `:-` on the outer default: an explicitly empty endpoint must reach the exit-78 check
  // rather than being quietly replaced by the baked-in URL.
  assert.doesNotMatch(
    text,
    /HARNESS_ENDPOINT="\$\{HARNESS_ENDPOINT:-/,
    "an empty HARNESS_ENDPOINT is a configuration error, not an unset one",
  );
});

test("claude-aify wrapper: unattended bypass is on by default and --safe opts out", () => {
  // 2026-06-02 decision: aify agents run unattended and must not stall on approval prompts.
  const text = sharedText();
  assert.match(text, /^CLAUDE_AUTO=true$/m, "bypass must default on");
  assert.match(
    text,
    /if \[ "\$CLAUDE_AUTO" = true \]; then\s*\n\s*CLAUDE_PERMISSION_FLAGS\+=\(--dangerously-skip-permissions\)/,
    "the default must actually add the bypass flag",
  );
  assert.match(text, /"--safe"/, "--safe must be accepted");
  assert.match(text, /CLAUDE_AUTO=false/, "--safe/--no-auto must be able to turn it off");
  assert.match(
    text,
    /"\$\{CLAUDE_PERMISSION_FLAGS\[@\]\}"/,
    "the permission flags must reach the claude invocation",
  );
});

test("claude-aify wrapper: an identity-less session says so instead of latching silently", () => {
  // 2026-07-14: without AIFY_AGENT_ID every turn-state path is dead, the channel sidecar still sets
  // `working` on an inbound wake, and nothing alive can clear it. It cost days of "general-manager is
  // always working". Anonymous sessions stay legal; they just may not be silent.
  const text = sharedText();
  assert.match(text, /NO AGENT ID/, "an id-less session must warn on stderr");
  assert.match(text, /--aify-agent/, "the warning must name the flag that fixes it");
});

test("claude-aify wrapper: the session-capture hooks are installed on every launch", () => {
  // Session-id truth (#138): the SessionStart/UserPromptSubmit hook keys claude's own session id by
  // AIFY_AGENT_ID. It is what stops a team sharing one directory from adopting each other's sessions,
  // and it is the store the register path now falls back to.
  const text = sharedText();
  assert.match(text, /"SessionStart"/, "SessionStart hook must be configured");
  assert.match(text, /"UserPromptSubmit"/, "UserPromptSubmit hook must be configured");
  assert.match(text, /claude-session-hook\.js/, "both must point at the capture hook");
  assert.match(text, /CLAUDE_MCP_FLAGS\+=\(--settings /, "the settings file must reach claude");
});

test("claude-aify wrapper: temp config files are cleaned up on exit", () => {
  const text = sharedText();
  assert.match(text, /trap 'rm -f "\$AIFY_MCP_CONFIG" "\$AIFY_HOOK_SETTINGS"/, "must trap EXIT");
});

test("claude-aify wrapper: the channel server is loaded so resident wake works", () => {
  const text = sharedText();
  assert.match(
    text,
    /--dangerously-load-development-channels server:aify-comms-channel/,
    "resident wake depends on the channel server being loaded",
  );
  assert.match(text, /export AIFY_CHANNELS_ENABLED="1"/, "registration must know channels are on");
});
