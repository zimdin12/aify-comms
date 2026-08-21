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

// 1. appServerUrl is set. Send a path native to the OS that will deserialize
//    it. On Windows that means a forward-slash drive-letter path; on Linux/WSL
//    a Windows registration path must be converted to /mnt/<drive>/... .
const residentWin = resolveCodexRequestCwdFor({
  hostCwd: "C:\\Docker\\sample-project",
  appServerUrl: "ws://127.0.0.1:55555",
  legacyTransform: wslTransform,
});
assert.equal(
  residentWin,
  process.platform === "win32" ? "C:/Docker/sample-project" : "/mnt/c/Docker/sample-project",
  "resident (appServerUrl set) must send a path format native to the Codex host OS",
);

// 2. Same case but the input already uses forward slashes.
const residentWinFwd = resolveCodexRequestCwdFor({
  hostCwd: "C:/Docker/sample-project",
  appServerUrl: "ws://127.0.0.1:55555",
  legacyTransform: wslTransform,
});
assert.equal(
  residentWinFwd,
  process.platform === "win32" ? "C:/Docker/sample-project" : "/mnt/c/Docker/sample-project",
);

// 3. No appServerUrl → we're about to spawn our own Codex via the legacy
//    launcher, so the legacy transform applies. On Windows that means
//    /mnt/c/..., which is correct when we're spawning wsl.exe.
const managedWin = resolveCodexRequestCwdFor({
  hostCwd: "C:\\Docker\\sample-project",
  appServerUrl: "",
  legacyTransform: wslTransform,
});
assert.equal(
  managedWin,
  "/mnt/c/Docker/sample-project",
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
  hostCwd: "C:\\Docker/sample-project\\subdir",
  appServerUrl: "ws://127.0.0.1:55555",
  legacyTransform: wslTransform,
});
assert.equal(
  mixed,
  process.platform === "win32" ? "C:/Docker/sample-project/subdir" : "/mnt/c/Docker/sample-project/subdir",
);

console.log("codex-cwd-transform.test.js: all assertions passed");
