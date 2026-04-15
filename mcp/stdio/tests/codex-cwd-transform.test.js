#!/usr/bin/env node
// Regression test: the cwd format we send to Codex over JSON-RPC must
// match the OS of the Codex process that deserializes it.
//
// The bug this locks down:
//   On Windows, defaultCodexCommand() returns "wsl.exe -e codex app-server",
//   so isWslCodexLauncher() is true by default, so codexWorkingPath()
//   runs every cwd through toWslPath() and returns "/mnt/c/...".
//   That's correct when we're about to spawn a WSL-hosted Codex ourselves.
//   It is WRONG when we're connecting to an existing native-Windows Codex
//   app-server launched by codex-aify: Rust's Path::is_absolute() returns
//   false for "/mnt/c/..." (no drive-letter prefix), so AbsolutePathBuf
//   rejects the request with "AbsolutePathBuf deserialized without a base
//   path" and every resident dispatch on Windows fails.
//
// Rule: resolveCodexRequestCwdFor must skip the launcher-based transform
// whenever appServerUrl is set, because codex-aify always launches a
// native Codex on the host OS.
//
// Run:  node mcp/stdio/tests/codex-cwd-transform.test.js

import assert from "node:assert/strict";
import { resolveCodexRequestCwdFor } from "../codex-errors.js";

// The "legacy transform" in production is codexWorkingPath(launcher, cwd),
// which on Windows turns "C:\\foo" into "/mnt/c/foo" when the launcher is
// wsl.exe. For the test we stub it with a function that ALWAYS applies the
// WSL transform so we can prove the guard short-circuits when appServerUrl
// is set.
const wslTransform = (raw) => {
  const normalized = String(raw || "").replace(/\\/g, "/");
  const match = normalized.match(/^([A-Za-z]):\/(.*)$/);
  if (!match) return normalized;
  return `/mnt/${match[1].toLowerCase()}/${match[2]}`;
};

// 1. codex-aify case: appServerUrl is set. Must NOT apply the WSL transform.
//    The native Windows Codex on the other end of the WebSocket needs a
//    drive-letter path so Path::is_absolute() is true.
const residentWin = resolveCodexRequestCwdFor({
  hostCwd: "C:\\Docker\\aify-project-graph",
  appServerUrl: "ws://127.0.0.1:55555",
  legacyTransform: wslTransform,
});
assert.equal(
  residentWin,
  "C:/Docker/aify-project-graph",
  "resident (appServerUrl set) must send forward-slash Windows path, not /mnt/c/...",
);

// 2. Same case but the input already uses forward slashes.
const residentWinFwd = resolveCodexRequestCwdFor({
  hostCwd: "C:/Docker/aify-project-graph",
  appServerUrl: "ws://127.0.0.1:55555",
  legacyTransform: wslTransform,
});
assert.equal(residentWinFwd, "C:/Docker/aify-project-graph");

// 3. No appServerUrl → we're about to spawn our own Codex via the legacy
//    launcher, so the legacy transform applies. On Windows that means
//    /mnt/c/..., which is correct when we're spawning wsl.exe.
const managedWin = resolveCodexRequestCwdFor({
  hostCwd: "C:\\Docker\\aify-project-graph",
  appServerUrl: "",
  legacyTransform: wslTransform,
});
assert.equal(
  managedWin,
  "/mnt/c/Docker/aify-project-graph",
  "managed (no appServerUrl) must defer to legacy launcher transform",
);

// 4. Linux host with codex-aify. No backslashes to normalize; cwd is already
//    a valid native path. Must be unchanged.
const residentLinux = resolveCodexRequestCwdFor({
  hostCwd: "/home/user/project",
  appServerUrl: "ws://127.0.0.1:66666",
  legacyTransform: wslTransform,
});
assert.equal(residentLinux, "/home/user/project");

// 5. Empty / undefined cwd degrades gracefully to "".
const emptyResident = resolveCodexRequestCwdFor({
  hostCwd: undefined,
  appServerUrl: "ws://127.0.0.1:77777",
  legacyTransform: wslTransform,
});
assert.equal(emptyResident, "");

const emptyManaged = resolveCodexRequestCwdFor({
  hostCwd: null,
  appServerUrl: "",
  legacyTransform: wslTransform,
});
assert.equal(emptyManaged, "");

// 6. Mixed separators (the exact shape Windows users pass into comms_register
//    when they copy a path from Explorer). Both branches must collapse to a
//    single consistent form.
const mixed = resolveCodexRequestCwdFor({
  hostCwd: "C:\\Docker/aify-project-graph\\subdir",
  appServerUrl: "ws://127.0.0.1:55555",
  legacyTransform: wslTransform,
});
assert.equal(mixed, "C:/Docker/aify-project-graph/subdir");

console.log("codex-cwd-transform.test.js: all assertions passed");
