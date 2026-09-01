#!/usr/bin/env node
// One host gets ONE kind, whatever launched the process.
//
// THE DEFECT. `environmentKind` derived WSL from `WSL_DISTRO_NAME`. The environment id is
// `${kind}:${hostname}:default` (environment-identity.mjs) and nothing sets AIFY_ENVIRONMENT_ID, so
// the kind IS the identity of the row. That variable is present in interactive shells and absent in
// many child processes -- which is not a guess: `stablePlatformTag` in runtimes.js stopped trusting
// it on 2026-06-02, when one WSL host registered as both `wsl-ubuntu:host` and `linux:host` and a
// delivery loop could never claim runs for an agent recorded under the other spelling. The fix was
// applied to machine_id and not to kind, so the same divergence sat one field over, on the key an
// environment row is matched on -- the exact failure environment-identity.mjs's own header warns
// about: "two bridges that described the same host differently would register as two environments
// and split its workers between them".
//
// WHY THE SIGNAL SITS BELOW THE DOCKER CHECK, which is the half that is easy to get wrong: a docker
// container running ON WSL2 reads "microsoft" in /proc/sys/kernel/osrelease too, because it is the
// WSL2 kernel. Letting the file speak before the container check would relabel every such container
// `wsl`. The last test here is that contradiction arm; without it this file would pass just as
// happily on the broken ordering.

import assert from "node:assert/strict";
import { test } from "node:test";

import { hostIsWsl } from "../runtimes.js";
import { environmentHeartbeatPayload, environmentKind, environmentOs } from "../environment-identity.mjs";

/** A /proc reader that answers with whatever this host is being posed as. */
const reads = (text) => () => text;
const unreadable = () => { throw new Error("ENOENT"); };

/** The env vars `environmentKind` still consults. Sealed, or this file measures the operator. */
const AMBIENT = ["AIFY_ENVIRONMENT_KIND", "WSL_DISTRO_NAME", "container"];

function sealed(env, run) {
  const saved = new Map(AMBIENT.map((name) => [name, process.env[name]]));
  for (const name of AMBIENT) delete process.env[name];
  Object.assign(process.env, env);
  try {
    for (const name of AMBIENT) {
      if (!(name in env)) {
        assert.equal(process.env[name], undefined, `${name} leaked into a case that does not set it`);
      }
    }
    return run();
  } finally {
    for (const name of AMBIENT) delete process.env[name];
    for (const [name, value] of saved) if (value !== undefined) process.env[name] = value;
  }
}

// -- the probe ---------------------------------------------------------------------------------

test("the probe says YES on a WSL kernel", () => {
  assert.equal(hostIsWsl({ platform: "linux", readFile: reads("5.15.0-microsoft-standard-WSL2") }), true);
});

test("the probe says NO on an ordinary linux kernel", () => {
  // NEGATIVE CONTROL. A probe that cannot return false cannot return true either.
  assert.equal(hostIsWsl({ platform: "linux", readFile: reads("6.1.0-generic") }), false);
});

test("the probe is platform-gated, so no other OS can read a linux path", () => {
  assert.equal(hostIsWsl({ platform: "win32", readFile: reads("microsoft") }), false);
  assert.equal(hostIsWsl({ platform: "darwin", readFile: reads("microsoft") }), false);
});

test("an unreadable /proc fails CLOSED", () => {
  // A guard that passed when its input was missing would call every unreadable host WSL.
  assert.equal(hostIsWsl({ platform: "linux", readFile: unreadable }), false);
});

// -- the kind ----------------------------------------------------------------------------------

test("THE REGRESSION: a WSL host with no WSL_DISTRO_NAME is `wsl`, not `linux`", () => {
  // This is the case that produced two environment rows for one machine. Before the fix it returned
  // the running platform, which on a WSL host is `linux`.
  sealed({}, () => assert.equal(environmentKind({ isWsl: true }), "wsl"));
});

test("the variable still answers on its own, so nothing that worked stopped working", () => {
  sealed({ WSL_DISTRO_NAME: "Ubuntu" }, () => assert.equal(environmentKind({ isWsl: false }), "wsl"));
});

test("an explicit kind still wins outright", () => {
  sealed({ AIFY_ENVIRONMENT_KIND: "custom-tier" }, () =>
    assert.equal(environmentKind({ isWsl: true }), "custom-tier"));
});

test("WSL still beats container, which is a declared precedence", () => {
  sealed({ WSL_DISTRO_NAME: "Ubuntu", container: "podman" }, () =>
    assert.equal(environmentKind({ isWsl: true }), "wsl"));
});

test("CONTRADICTION ARM: a docker container running ON WSL2 stays `docker`", () => {
  // The WSL2 kernel says "microsoft" in osrelease inside the container as well, so the probe is TRUE
  // here and is right to be. Ordering is what keeps the answer correct. Put the file signal above
  // the container check and this is the only test in the file that notices.
  sealed({ container: "podman" }, () => assert.equal(environmentKind({ isWsl: true }), "docker"));
});

test("a host the probe says NO about never becomes `wsl`", () => {
  // The negative arm of the regression above. Stated as "not wsl" rather than "linux" because the
  // platform fallbacks read the REAL process.platform -- this file cannot pose one, and asserting
  // `linux` here would only ever pass on a linux runner while looking like a general claim.
  sealed({}, () => assert.notEqual(environmentKind({ isWsl: false }), "wsl"));
});

test("kind and os stay different questions", () => {
  // `wsl` is not an operating system. Collapsing them would advertise one as the other.
  sealed({}, () => {
    assert.equal(environmentKind({ isWsl: true }), "wsl");
    assert.notEqual(environmentOs(), "wsl");
  });
});

// -- why the kind matters at all -----------------------------------------------------------------

test("the environment id is built FROM the kind, which is why a wrong kind splits a host", () => {
  // Not a restatement: this is the join that turns a label into an identity, asserted by BUILDING a
  // payload rather than by matching the source line that builds it. If the id stopped carrying the
  // kind, every test above would be about a display string and this file would be over-claiming.
  const saved = process.env.AIFY_ENVIRONMENT_ID;
  delete process.env.AIFY_ENVIRONMENT_ID;
  try {
    const payload = environmentHeartbeatPayload({ terminalSupported: false });
    assert.equal(
      payload.id.startsWith(`${payload.kind}:`), true,
      `the environment id ${payload.id} no longer begins with its kind ${payload.kind} -- re-read `
      + "what a wrong kind now costs before trusting the cases above",
    );
  } finally {
    if (saved !== undefined) process.env.AIFY_ENVIRONMENT_ID = saved;
  }
});
