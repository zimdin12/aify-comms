#!/usr/bin/env node
// Delegation hands aify-env the launcher, not the Windows shim that calls it.
//
// MEASURED ON THE REAL HOST, 2026-08-25, which is how this was found:
//
//   resolveExecutable("claude-aify") -> C:\Users\...\.local\bin\claude-aify.cmd
//   aify-env's allowlist on that     -> REFUSED, "no shebang on the first line"
//   the sibling with no extension    -> ACCEPTED
//
// So flipping delegation on a Windows host refused every managed spawn while every component looked
// healthy: the command resolved, the file existed, and the environment was answering. The failure was
// that "what Windows would execute" and "which file is the launcher" are different questions, and the
// delegated path was asking the first one.
//
// On Linux the two are the same path and this changes nothing, which is exactly why it went unnoticed
// in a seam "proven against a real aify-env".

import assert from "node:assert/strict";
import { test } from "node:test";

import { launcherCandidates, launcherFileFor } from "../launcher-file.mjs";

const LAUNCHER = ['#!/usr/bin/env bash', 'HARNESS_WRAPPER_VERSION="0.6.0"', 'exec claude "$@"'].join("\n");
const SHIM = ['@echo off', 'bash "%~dp0claude-aify" %*'].join("\r\n");

test("a Windows shim yields the extensionless sibling FIRST", () => {
  // The ordering is the whole decision. Preferring the shim is what made delegation impossible.
  assert.deepEqual(
    launcherCandidates("C:/bin/claude-aify.cmd"),
    ["C:/bin/claude-aify", "C:/bin/claude-aify.cmd"],
  );
});

test("every shim extension is recognised, whatever its case", () => {
  for (const path of ["C:/b/x.CMD", "C:/b/x.Bat", "C:/b/x.EXE", "C:/b/x.ps1"]) {
    assert.equal(launcherCandidates(path)[0], "C:/b/x", `${path} was not stripped`);
  }
});

test("a path with no shim extension is its own only candidate", () => {
  // Linux, where the launcher IS what resolves. Adding candidates here would invent files.
  assert.deepEqual(launcherCandidates("/home/dev/.local/bin/claude-aify"),
    ["/home/dev/.local/bin/claude-aify"]);
});

test("an empty resolution yields nothing rather than a bare extension", () => {
  assert.deepEqual(launcherCandidates(""), []);
  assert.deepEqual(launcherCandidates(null), []);
  assert.deepEqual(launcherCandidates(undefined), []);
});

test("the launcher is chosen over the shim that resolved", () => {
  const files = { "C:/bin/claude-aify": LAUNCHER, "C:/bin/claude-aify.cmd": SHIM };
  const found = launcherFileFor("C:/bin/claude-aify.cmd", (p) => {
    if (!(p in files)) throw new Error("ENOENT");
    return files[p];
  });
  assert.equal(found.path, "C:/bin/claude-aify");
});

test("a shim with no launcher beside it is not a launcher, and says what was tried", () => {
  // Refusing is right -- aify-env would refuse it anyway -- but the caller has to be able to name the
  // files it looked at, or the operator sees "not a launcher" about a path that plainly exists.
  const found = launcherFileFor("C:/bin/claude-aify.cmd", (p) => {
    if (p.endsWith(".cmd")) return SHIM;
    throw new Error("ENOENT");
  });
  assert.equal(found, null);
});

test("an unreadable candidate is skipped, not fatal", () => {
  const found = launcherFileFor("C:/bin/x.cmd", (p) => {
    if (p.endsWith(".cmd")) return LAUNCHER;
    throw new Error("EACCES");
  });
  assert.equal(found.path, "C:/bin/x.cmd", "a readable shim carrying the marker is still a launcher");
});

test("a file without the marker is refused even when it is the only candidate", () => {
  const found = launcherFileFor("/usr/bin/claude", () => "#!/bin/sh\nexec claude\n");
  assert.equal(found, null);
});

test("the marker is matched on its own line, not anywhere in the text", () => {
  // A launcher that merely MENTIONS the contract in prose is documentation. aify-env draws the same
  // distinction; this only has to agree with it well enough to rank candidates.
  const prose = "#!/bin/sh\n# see HARNESS_WRAPPER_VERSION in the docs\nexec claude\n";
  assert.equal(launcherFileFor("/usr/bin/claude", () => prose), null);
});
