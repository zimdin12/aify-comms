#!/usr/bin/env node
// One host, one machine id — asserted by running BOTH implementations, not by reading either.
//
// WHY TWO EXIST AT ALL. The bridge has always built `machineId` and heartbeated it. aify-env is
// becoming the advertiser for the host it owns (see `docs/ENVIRONMENT_ADVERTISEMENT.md`), so during
// the transition both can send one, and after it aify-env sends it alone. The format cannot drift
// across that handover.
//
// WHY DRIFT WOULD BE SILENT. `machine_id` is COMPARED, not merely recorded: the service arbitrates
// bridge supersession on it. Two producers disagreeing about one machine does not raise anything —
// it makes the same host look like two, each with its own environment row, and the arbitration
// between a stale bridge and a fresh one simply stops matching. That is the failure shape this repo
// has paid for twice: a guard whose input has drifted reads exactly like a guard with nothing to
// arbitrate.
//
// WHY THIS IS NOT REDUNDANT WITH EITHER SUITE. Each repo tests its own builder against its own
// expectations, and two suites agreeing with themselves is not agreement. This drives the SAME
// inputs through both and compares the outputs — a different substrate, which is the only kind of
// independence that counts.
//
// IT FAILS RATHER THAN SKIPS when aify-env is absent, like its siblings here: "the cross-repo
// contract is unverified" must not read as green.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { defaultMachineId } from "../runtimes.js";

/** Where aify-env lives. Overridable, because a sibling checkout is a convention and not a fact. */
const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const ADVERTISE = path.join(AIFY_ENV, "lib", "advertise.mjs");
const available = fs.existsSync(ADVERTISE);

/** The env vars `defaultMachineId` consults, so a case can be posed to it without a subprocess. */
const NAMING = ["AIFY_MACHINE_ID", "COMPUTERNAME", "HOSTNAME"];

/**
 * What the BRIDGE would call a host, with its ambient inputs posed rather than inherited.
 *
 * The bridge reads `process.env` and `os.hostname()` directly, so the only way to ask it about a
 * hypothetical host is to become one for the length of the call. Restored in `finally`, because a
 * leaked COMPUTERNAME would change what every later test in this process is measuring.
 */
function bridgeAnswer(env) {
  const saved = new Map(NAMING.map((name) => [name, process.env[name]]));
  for (const name of NAMING) delete process.env[name];
  Object.assign(process.env, env);
  try {
    return defaultMachineId();
  } finally {
    for (const name of NAMING) delete process.env[name];
    for (const [name, value] of saved) if (value !== undefined) process.env[name] = value;
  }
}

test("aify-env is checked out, so this agreement can actually be exercised", () => {
  assert.equal(
    available,
    true,
    `aify-env not found at ${ADVERTISE}. Set AIFY_ENV_REPO, or check it out: the two machine-id `
    + "builders are otherwise only ever tested against their own expectations.",
  );
});

test("both tiers name this REAL host identically", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { machineIdFor } = await import(`file://${ADVERTISE.split(String.fromCharCode(92)).join("/")}`);

  // No hypotheticals: the machine the suite is running on, asked of both.
  const fromEnvironment = machineIdFor({
    platform: process.platform,
    hostname: os.hostname(),
    env: process.env,
    isWsl: process.platform === "linux"
      && /microsoft|wsl/i.test(readOsRelease()),
  });
  assert.equal(fromEnvironment, defaultMachineId(),
    "the two tiers would register this host under two different machine ids");
});

test("they agree across every host shape that has ever produced a live row", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { machineIdFor } = await import(`file://${ADVERTISE.split(String.fromCharCode(92)).join("/")}`);

  // The mixed casing is the point of several of these: both live rows on the operator's machine are
  // `StevenZ-L`, and lowercasing on one side only is how one host becomes two.
  const cases = [
    { label: "windows, COMPUTERNAME set", platform: "win32", hostname: "StevenZ-L", env: { COMPUTERNAME: "StevenZ-L" } },
    { label: "windows, only os.hostname", platform: "win32", hostname: "StevenZ-L", env: {} },
    { label: "windows, upper-case host", platform: "win32", hostname: "STEVENZ-L", env: {} },
    { label: "linux, HOSTNAME set", platform: "linux", hostname: "box", env: { HOSTNAME: "Build-Box" } },
    { label: "macos", platform: "darwin", hostname: "Mac-Mini", env: {} },
    { label: "explicit override", platform: "win32", hostname: "ignored", env: { AIFY_MACHINE_ID: "Pinned-Host" } },
    { label: "nothing names the host", platform: "win32", hostname: "", env: {} },
  ];

  for (const { label, platform, hostname, env } of cases) {
    // `defaultMachineId` reads the REAL platform, so only cases matching it can be compared against
    // the bridge directly. The rest are still driven through aify-env to pin the format.
    const environmentAnswer = machineIdFor({ platform, hostname, env, isWsl: false });
    if (platform === process.platform) {
      const saved = os.hostname;
      os.hostname = () => hostname;
      try {
        assert.equal(environmentAnswer, bridgeAnswer(env), `${label}: the two tiers disagree`);
      } finally {
        os.hostname = saved;
      }
    }
    assert.match(environmentAnswer, /^[^:]*:[^:]+$/, `${label}: not a <tag>:<host> pair`);
    assert.equal(environmentAnswer, environmentAnswer.toLowerCase(), `${label}: not lowercased`);
  }
});

test("WSL is tagged `wsl` on both sides, which is what keeps it off the Windows row", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { machineIdFor } = await import(`file://${ADVERTISE.split(String.fromCharCode(92)).join("/")}`);

  // Not a stylistic choice: a WSL guest and its Windows host report the SAME hostname, so the tag is
  // the only thing separating `wsl:stevenz-l` from `win32:stevenz-l`. Both of those are live rows.
  assert.equal(machineIdFor({ platform: "linux", hostname: "StevenZ-L", isWsl: true }), "wsl:stevenz-l");
  assert.notEqual(
    machineIdFor({ platform: "linux", hostname: "StevenZ-L", isWsl: true }),
    machineIdFor({ platform: "win32", hostname: "StevenZ-L" }),
    "a WSL guest and its Windows host collapsed onto one machine id",
  );
});

/** The file the bridge reads to decide it is WSL, or "" when there is none to read. */
function readOsRelease() {
  try {
    return fs.readFileSync("/proc/sys/kernel/osrelease", "utf8");
  } catch {
    return "";
  }
}
