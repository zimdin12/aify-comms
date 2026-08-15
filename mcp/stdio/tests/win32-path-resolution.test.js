#!/usr/bin/env node
// Regression: executable resolution on Windows must survive non-ASCII user
// paths (C:\Users\KertMõttus). The old resolver shelled out to `where`, whose
// OEM-codepage stdout lossily transcodes õ -> o, so the printed path did not
// exist on disk and runtimeLaunchAvailability declared claude-code
// unlaunchable — managed spawns then died "up-but-deaf". The fix walks PATH
// in-process (resolveOnWindowsPath) and treats `where` output as a hint that
// must exist on disk. Also covers PS_UTF8_PRELUDE wiring for the PowerShell
// process inspectors, whose command-line output has the same OEM problem.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { resolveOnWindowsPath } from "../runtimes-exec.js";
import { PS_UTF8_PRELUDE } from "../win32-text.js";
import { defaultGetCmdline as hermesGetCmdline } from "../hermes-daemon.js";
import { defaultGetCmdline as reapGetCmdline, defaultListClaudeProcs } from "../reap-managed-claude.js";
// `defaultListProcesses` moved to `proc-probes.js` in v0.5.4 with the rest of the process read side.
import { defaultListProcesses } from "../proc-probes.js";
import { tmpDir } from "./_tmpdir.js";

function makeExecutable(filePath, content) {
  fs.writeFileSync(filePath, content);
  fs.chmodSync(filePath, 0o755); // isReallyExecutable checks X_OK off-Windows
}

// A PATH directory with the exact shape that broke in the field: non-ASCII
// profile segment, wrapper bash script + .cmd shim side by side.
const profileDir = tmpDir("aify-KertMõttus-");
const binDir = path.join(profileDir, ".local", "bin");
fs.mkdirSync(binDir, { recursive: true });
makeExecutable(path.join(binDir, "claude-aify"), "#!/bin/bash\necho wrapper\n");
makeExecutable(path.join(binDir, "claude-aify.cmd"), "@echo off\r\n");

const resolved = resolveOnWindowsPath("claude-aify", { pathString: binDir, pathExtString: ".COM;.EXE;.BAT;.CMD" });
assert.equal(
  resolved,
  path.join(binDir, "claude-aify.cmd"),
  "wrapper in a non-ASCII profile dir must resolve, preferring the .cmd shim over the bare bash script",
);

// Per-directory Windows semantics: the first PATH dir containing any match
// wins, even when a later dir holds a "better" extension.
const dirA = tmpDir("aify-patha-");
const dirB = tmpDir("aify-pathb-");
makeExecutable(path.join(dirA, "tool.bat"), "@echo off\r\n");
makeExecutable(path.join(dirB, "tool.exe"), "MZ");
assert.equal(
  resolveOnWindowsPath("tool", { pathString: `${dirA};${dirB}`, pathExtString: ".COM;.EXE;.BAT;.CMD" }),
  path.join(dirA, "tool.bat"),
  "first PATH directory containing a match must win",
);

// A quoted PATH entry (cmd.exe tolerates "C:\Program Files\x") still resolves.
assert.equal(
  resolveOnWindowsPath("tool", { pathString: `"${dirB}"`, pathExtString: ".EXE" }),
  path.join(dirB, "tool.exe"),
  "quoted PATH entries must be unwrapped before joining",
);

// A name that already carries a Windows extension is matched exactly.
assert.equal(
  resolveOnWindowsPath("tool.exe", { pathString: dirB, pathExtString: ".COM;.EXE" }),
  path.join(dirB, "tool.exe"),
  "explicit extension must match the exact file",
);

// Bare extension-less file is a last resort — only returned when no PATHEXT
// sibling exists anywhere in that directory.
const dirC = tmpDir("aify-pathc-");
makeExecutable(path.join(dirC, "onlyscript"), "#!/bin/bash\n");
assert.equal(
  resolveOnWindowsPath("onlyscript", { pathString: dirC, pathExtString: ".COM;.EXE;.BAT;.CMD" }),
  path.join(dirC, "onlyscript"),
  "extension-less script must still resolve when it is the only candidate",
);

// Misses return null (not a mangled guess).
assert.equal(
  resolveOnWindowsPath("no-such-tool", { pathString: `${dirA};${dirC}`, pathExtString: ".EXE" }),
  null,
  "unresolvable names must return null",
);

// Names that already contain a separator are the caller's absolute-path
// contract (resolveExecutable handles those) — the PATH walker refuses them.
assert.equal(
  resolveOnWindowsPath(path.join(dirB, "tool.exe"), { pathString: dirB }),
  null,
  "path-like inputs are not PATH-walked",
);

// PS_UTF8_PRELUDE must actually reach every PowerShell inspector that parses
// path-bearing output. The win32 branch only runs on Windows; elsewhere we
// still verify the constant's shape so a refactor can't silently drop it.
assert.ok(
  PS_UTF8_PRELUDE.includes("[Console]::OutputEncoding") && PS_UTF8_PRELUDE.includes("UTF8"),
  "PS_UTF8_PRELUDE must force UTF-8 console output",
);
if (process.platform === "win32") {
  const captured = [];
  const fakeSpawnSync = (cmd, args) => {
    captured.push({ cmd, args });
    return { status: 0, stdout: "" };
  };
  hermesGetCmdline(1234, fakeSpawnSync);
  reapGetCmdline(1234, fakeSpawnSync);
  defaultListClaudeProcs(fakeSpawnSync);
  defaultListProcesses(fakeSpawnSync);
  assert.equal(captured.length, 4, "all four PowerShell inspectors should have been invoked");
  for (const { cmd, args } of captured) {
    assert.equal(cmd, "powershell.exe");
    const script = args[args.indexOf("-Command") + 1];
    assert.ok(
      script.startsWith(PS_UTF8_PRELUDE),
      `PowerShell inspector must lead with PS_UTF8_PRELUDE, got: ${script.slice(0, 80)}`,
    );
  }
}

console.log("win32-path-resolution: ok");
