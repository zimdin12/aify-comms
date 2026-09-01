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
import fs, { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { defaultMachineId } from "../runtimes.js";
import { environmentKind, environmentOs } from "../environment-identity.mjs";

/** Where aify-env lives. Overridable, because a sibling checkout is a convention and not a fact. */
const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const ADVERTISE = path.join(AIFY_ENV, "lib", "advertise.mjs");
const HOST_WSL = path.join(AIFY_ENV, "lib", "host-wsl.mjs");
const available = fs.existsSync(ADVERTISE);

/** aify-env's OWN WSL probe, so each tier answers with its own instrument rather than a shared one. */
async function environmentSaysWsl() {
  const mod = await import(`file://${HOST_WSL.split(String.fromCharCode(92)).join("/")}`);
  return mod.hostIsWsl();
}

/** The env vars `defaultMachineId` consults, so a case can be posed to it without a subprocess. */
const NAMING = ["AIFY_MACHINE_ID", "COMPUTERNAME", "HOSTNAME"];

/** The env vars `environmentKind` consults, for the same reason. */
const KIND_VARS = ["AIFY_ENVIRONMENT_KIND", "WSL_DISTRO_NAME", "container"];

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
  // `isWsl` COMES FROM aify-env's OWN PROBE, not from a copy of the rule written here. A test that
  // computes the input itself hands the tier a better answer than its production call site gets --
  // which is exactly how this drifted: `bin/aify-env.mjs` passed `isWsl: kind === "wsl"`, derived
  // from an environment variable, while the bridge read /proc. Both now probe; this asks each.
  const fromEnvironment = machineIdFor({
    platform: process.platform,
    hostname: os.hostname(),
    env: process.env,
    isWsl: await environmentSaysWsl(),
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

test("both tiers call this REAL host the same kind, and the same os", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const advertise = await import(`file://${ADVERTISE.split(String.fromCharCode(92)).join("/")}`);

  // No hypotheticals: the machine the suite is running on, asked of both.
  // EACH TIER USES ITS OWN PROBE. Passing one answer to both would compare a function against
  // itself: the drift being guarded here is precisely that the two could derive WSL differently,
  // which they did until `hostIsWsl` landed on both sides -- one read /proc while the other read
  // WSL_DISTRO_NAME, so a WSL host that did not inherit the variable got two kinds and two ids.
  assert.equal(
    advertise.environmentKind({
      platform: process.platform, env: process.env, exists: existsSync,
      isWsl: await environmentSaysWsl(),
    }),
    environmentKind(),
    "the two tiers would register this host under two different KINDS, and the kind is joined into the id",
  );
  assert.equal(advertise.environmentOs(process.platform), environmentOs());
});

test("they agree on every env-driven branch, which is where a drift would land", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const advertise = await import(`file://${ADVERTISE.split(String.fromCharCode(92)).join("/")}`);

  // These branches are read from the environment rather than from the platform, so both
  // implementations can be posed the same case on any machine. They are also the ones an operator
  // actually sets, and the ones a refactor is most likely to reorder.
  const cases = [
    { label: "an explicit kind wins outright", env: { AIFY_ENVIRONMENT_KIND: "custom-tier" } },
    { label: "an explicit kind beats WSL", env: { AIFY_ENVIRONMENT_KIND: "custom", WSL_DISTRO_NAME: "Ubuntu" } },
    { label: "WSL", env: { WSL_DISTRO_NAME: "Ubuntu" } },
    { label: "a container runtime", env: { container: "podman" } },
    { label: "WSL beats container", env: { WSL_DISTRO_NAME: "Ubuntu", container: "podman" } },
    { label: "nothing set — falls through to the platform", env: {} },
    { label: "an explicit kind of whitespace is not a kind", env: { AIFY_ENVIRONMENT_KIND: "   " } },
  ];

  // THE BRANCH THAT WAS BROKEN ON BOTH SIDES, posed to both: a WSL host whose process did not
  // inherit WSL_DISTRO_NAME. Driven separately from the env cases because it is the one input that
  // does NOT come from the environment, which is the entire reason it was got wrong.
  for (const isWsl of [true, false]) {
    assert.equal(
      advertise.environmentKind({ platform: process.platform, env: {}, exists: () => false, isWsl }),
      environmentKind({ isWsl }),
      `a host the probe calls ${isWsl ? "WSL" : "not WSL"} gets two different kinds`,
    );
  }

  const saved = new Map(KIND_VARS.map((name) => [name, process.env[name]]));
  try {
    for (const { label, env } of cases) {
      for (const name of KIND_VARS) delete process.env[name];
      Object.assign(process.env, env);
      const isWsl = await environmentSaysWsl();
      assert.equal(
        advertise.environmentKind({
          platform: process.platform, env: process.env, exists: existsSync, isWsl,
        }),
        environmentKind({ isWsl }),
        `${label}: the two tiers disagree`,
      );
    }
  } finally {
    for (const name of KIND_VARS) delete process.env[name];
    for (const [name, value] of saved) if (value !== undefined) process.env[name] = value;
  }
});

test("kind and os are different questions, on both sides", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const advertise = await import(`file://${ADVERTISE.split(String.fromCharCode(92)).join("/")}`);

  // A wsl host RUNS linux. Collapsing the two would advertise `os: wsl`, which is not an operating
  // system, and `kind: linux`, which loses the only thing distinguishing the host from the Windows
  // one it shares a hostname with. Both live rows here depend on that separation.
  const saved = process.env.WSL_DISTRO_NAME;
  process.env.WSL_DISTRO_NAME = "Ubuntu";
  try {
    assert.equal(environmentKind(), "wsl", "the bridge stopped recognising WSL");
    assert.notEqual(environmentOs(), "wsl", "the bridge reported a kind as an operating system");
    assert.equal(
      advertise.environmentKind({ platform: "linux", env: { WSL_DISTRO_NAME: "Ubuntu" } }), "wsl");
    assert.equal(advertise.environmentOs("linux"), "linux");
  } finally {
    if (saved === undefined) delete process.env.WSL_DISTRO_NAME;
    else process.env.WSL_DISTRO_NAME = saved;
  }
});
