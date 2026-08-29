#!/usr/bin/env node
// A runtime may only advertise itself launchable if the file a spawn would run actually resolves.
//
// TWO OF FIVE FAILED THIS, and both were found by reading the live environment row rather than the
// code. On 2026-08-30, with `pi-aify` and `opencode-aify` absent from this host:
//
//     opencode  available: true   reason "OpenCode SDK available"   -- nothing was probed at all
//     pi        available: true   reason ""                         -- `omp` was probed, not `pi-aify`
//
// The cost is not cosmetic. Since Phase 8 a managed spawn is DELEGATED to aify-env, which runs a file
// only when it carries the harness contract marker. `omp.exe` carries none, so the tier that owns
// processes refuses a pi spawn with 403 while the control plane says the runtime is available -- and
// `managed-environment-sync.mjs` reads exactly this field to decide what may be started. A wrong yes
// here becomes a spawn that fails with no cause attached to it.
//
// DERIVED OVER `ENVIRONMENT_RUNTIME_IDS`, so a runtime added later is covered without anyone
// remembering this file. That is the half a hand-listed test would have missed: opencode was added to
// that list and inherited a hardcoded yes nobody re-read.
//
// IT RUNS THE REAL PROBE. `environment-runtimes.test.js` injects `availabilityFor` and so cannot see
// any of this -- it was green throughout. The claim under test is about what the unmocked function
// says when the files are not there.

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { ENVIRONMENT_RUNTIME_IDS } from "../environment-runtimes.js";
import { forgetResolvedExecutables, runtimeLaunchAvailability } from "../runtimes.js";

const WINDOWS = process.platform === "win32";

/** Every env var that could let a real installation leak into a sealed run, restored afterwards. */
const AMBIENT = [
  "PATH", "Path", "PATHEXT",
  "AIFY_CLAUDE_COMMAND", "CLAUDE_COMMAND",
  "AIFY_CODEX_AIFY_COMMAND", "AIFY_HERMES_AIFY_COMMAND",
  "AIFY_OPENCODE_AIFY_COMMAND", "AIFY_PI_AIFY_COMMAND",
  "AIFY_PI_COMMAND", "PI_COMMAND",
];

function sealed(pathValue, body) {
  const saved = new Map(AMBIENT.map((name) => [name, process.env[name]]));
  for (const name of AMBIENT) delete process.env[name];
  // The resolution cache is ambient too, and sealing PATH without it seals nothing after the first
  // successful resolve in this process -- which is how this file first passed three runtimes that
  // were answering off deleted paths.
  forgetResolvedExecutables();
  process.env.PATH = pathValue;
  // Windows resolution needs an extension list; giving exactly one keeps the fixture names honest.
  if (WINDOWS) process.env.PATHEXT = ".CMD";
  try {
    // THE SEAL IS ASSERTED, not assumed. A test that reads the operator's live PATH looks identical
    // to one that does not, right up until it passes for the wrong reason on their machine.
    assert.equal(process.env.PATH, pathValue, "the PATH seal did not take");
    assert.equal(process.env.AIFY_PI_COMMAND, undefined, "a runtime override survived the seal");
    return body();
  } finally {
    for (const [name, value] of saved) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
}

test("with nothing on PATH, no runtime claims to be launchable", () => {
  // An empty PATH is the strongest form of "the file is not there". Any runtime still saying yes is
  // answering from something other than a file.
  sealed("", () => {
    for (const runtime of ENVIRONMENT_RUNTIME_IDS) {
      const verdict = runtimeLaunchAvailability(runtime);
      assert.equal(verdict.available, false,
        `${runtime} claimed to be launchable with an empty PATH: ${verdict.message}`);
      assert.notEqual(String(verdict.message || "").trim(), "",
        `${runtime} refused without saying why, which is what an operator reads`);
    }
  });
});

test("the probe can still say YES — the same run proves the instrument works", () => {
  // Without this the test above passes just as well on a probe that always refuses, and a gate that
  // cannot return PRESENT cannot return ABSENT.
  const dir = mkdtempSync(join(tmpdir(), "aify-launchable-"));
  try {
    for (const name of ["claude-aify", "codex-aify", "hermes-aify", "opencode-aify", "pi-aify"]) {
      writeFileSync(join(dir, WINDOWS ? `${name}.cmd` : name), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    }
    sealed(dir, () => {
      for (const runtime of ENVIRONMENT_RUNTIME_IDS) {
        const verdict = runtimeLaunchAvailability(runtime);
        assert.equal(verdict.available, true,
          `${runtime} refused a wrapper that is on PATH: ${verdict.message}`);
      }
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the file each runtime looks for is its own wrapper, named after it", () => {
  // The specific confusion this replaces: pi resolved `omp`, the runtime BEHIND the wrapper. Putting
  // one wrapper on PATH must make exactly one runtime available, so a probe reading a shared binary
  // shows up as a second runtime turning green.
  const dir = mkdtempSync(join(tmpdir(), "aify-one-wrapper-"));
  try {
    writeFileSync(join(dir, WINDOWS ? "pi-aify.cmd" : "pi-aify"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    sealed(dir, () => {
      const green = ENVIRONMENT_RUNTIME_IDS.filter((r) => runtimeLaunchAvailability(r).available);
      assert.deepEqual(green, ["pi"], "installing one wrapper changed another runtime's answer");
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a refusal names the wrapper an operator has to install", () => {
  // The reason is the whole product of a `false`. `managed-environment-sync` acts on the boolean; a
  // human acts on this string, and "not launchable" without a filename sends them reading source.
  sealed("", () => {
    for (const runtime of ENVIRONMENT_RUNTIME_IDS) {
      const { message } = runtimeLaunchAvailability(runtime);
      const wrapper = runtime === "claude-code" ? "claude-aify" : `${runtime}-aify`;
      assert.ok(message.includes(wrapper),
        `${runtime}'s refusal never names ${wrapper}: ${message}`);
    }
  });
});

test("the runtime binary being installed is not the wrapper being installed", () => {
  // THE LIVE HOST'S EXACT STATE, and the only shape that separates the two probes. An empty PATH
  // fails both, so a gate built only on that passes the shipped `omp` probe unchanged -- which it
  // did, on the first mutation run. `omp` here and no `pi-aify`: Oh My Pi is installed, and a
  // delegated spawn would still be refused by aify-env because `omp` carries no contract marker.
  const dir = mkdtempSync(join(tmpdir(), "aify-runtime-only-"));
  try {
    writeFileSync(join(dir, WINDOWS ? "omp.cmd" : "omp"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    sealed(dir, () => {
      const verdict = runtimeLaunchAvailability("pi");
      assert.equal(verdict.available, false,
        `pi read the runtime binary as a wrapper: ${verdict.message}`);
      assert.ok(verdict.message.includes("pi-aify"), "the refusal must name the missing wrapper");
      assert.match(verdict.message, /Oh My Pi itself IS installed/,
        "the two halves differ in what an operator does next, so the message must separate them");
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
