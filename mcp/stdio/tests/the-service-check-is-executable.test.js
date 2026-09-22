// The `service` check, RUN — which was impossible until it left doctor.js.
//
// WHY THIS FILE EXISTS. `doctor.js` runs its whole sequence at module scope and ends in
// `process.exit()`, so importing it runs the doctor. `checkService` could therefore only be asserted
// ABOUT, never called -- and that is exactly how it came to bypass its own verdict: an early return
// answered the no-checkout case itself, with `ok: true` and the payload's own short, so on any host
// without a repo an env-supplied build identity read as HEALTHY. Every test in the suite passed,
// because none of them could reach the call site.
//
// A main-guard on doctor.js would also have made it importable and is worse: the CLI is reached
// through a .cmd shim on Windows, and a guard that mis-resolves `process.argv[1]` makes the doctor
// silently do NOTHING -- the worst failure for the tool the operator uses to find out what is running.
// Moving the logic to a module that does not self-execute buys the same testability with none of that,
// and `aify-comms doctor` was run against the live service afterwards to prove the CLI still works.

import assert from "node:assert/strict";
import net from "node:net";
import { test } from "node:test";

import { checkService } from "../service-check.mjs";

/** A recorder for `add`, plus canned service answers. Nothing here touches a network or a checkout. */
function harness({ health = { status: "healthy" }, version = {}, repo = null, error = null } = {}) {
  const recorded = [];
  return {
    recorded,
    deps: {
      get: async (path) => (path === "/health" ? health : version),
      add: (...args) => { recorded.push(args); return args; },
      sh: () => "0",
      repo,
      serverUrl: "http://127.0.0.2:1",
      transportError: () => error,
    },
  };
}

test("an unreachable service is reported, and nothing else is claimed about it", async () => {
  const { recorded, deps } = harness({ health: null });
  await checkService(deps);
  const [name, ok, code] = recorded[0];
  assert.equal(name, "service");
  assert.equal(ok, false);
  assert.equal(code, "unreachable");
});

test("NO CHECKOUT still routes through the verdict — the bypass this extraction was for", async () => {
  // The defect, executed. Before, this path returned ok:true without consulting the verdict at all, so
  // an env-supplied identity was certified on precisely the hosts that have no repo to compare against.
  const { recorded, deps } = harness({
    version: { sha: "cafebabe1234", sha_short: "LIESHORT", identityOverriddenBy: ["build_sha"] },
    repo: null,
  });
  await checkService(deps);
  const [, ok, code, detail] = recorded[0];
  assert.equal(ok, false, "an env-supplied build identity was certified because there was no checkout");
  assert.equal(code, "build-identity-overridden");
  assert.doesNotMatch(detail, /LIESHORT/, "the supplied short was reported as the running build");
});

test("an ordinary repo-less service is still certified", async () => {
  // ANTI-VACUITY. Routing everything through the verdict must not turn every host without a checkout
  // red -- that would be a worse instrument than the one it replaced, not a better one.
  const { recorded, deps } = harness({
    version: { sha: "cafebabe1234", sha_short: "cafebab" },
    repo: null,
  });
  await checkService(deps);
  const [, ok, code, detail] = recorded[0];
  assert.equal(ok, true, `a clean repo-less service was refused: ${detail}`);
  assert.equal(code, "ok");
  assert.match(detail, /no checkout to compare against/);
});

test("a service reporting no sha is unknown-build", async () => {
  // The other case the call site used to answer for itself instead of delegating.
  const { recorded, deps } = harness({ version: { sha_short: "" }, repo: { sha: "f00d", short: "f00d", dir: "." } });
  await checkService(deps);
  const [, ok, code] = recorded[0];
  assert.equal(ok, false);
  assert.equal(code, "unknown-build");
});

test("a build matching HEAD is certified, and the git counts are read through `sh`", async () => {
  const { recorded, deps } = harness({
    version: { sha: "cafebabe1234", sha_short: "cafebab" },
    repo: { sha: "cafebabe1234", short: "cafebab", dir: "." },
  });
  let shCalls = 0;
  deps.sh = () => { shCalls += 1; return "0"; };
  await checkService(deps);
  const [, ok, , detail] = recorded[0];
  assert.equal(ok, true, detail);
  assert.match(detail, /== repo HEAD/);
  assert.equal(shCalls, 2, "the two commit counts were not both read; a git call was skipped");
});

test("every collaborator is REQUIRED, so a caller cannot silently get the module's own", async () => {
  // The parameters have no defaults on purpose: a test that forgot one would otherwise reach the real
  // network or the operator's real checkout, and pass for a reason nobody chose.
  await assert.rejects(() => checkService({}), "checkService ran with no collaborators at all");
});

// A PORT THAT ACCEPTS AND THEN RESETS IS NOT A SERVICE THAT IS DOWN. Seen on the operator's host on
// 2026-09-17 and 2026-09-21: after the WSL distro restarted, Docker Desktop's forward for the published
// ports accepted and reset every connection while the containers stayed healthy inside the VM. The
// row said "No healthy service" and told the operator to `up -d --build`, which leaves an unchanged
// container (and its dead forward) in place; `--force-recreate` is what restored it.
//
// The errors below are REAL: fetch against a real socket that is accepted and then dropped, in both
// shapes undici reports it (a plain close is UND_ERR_SOCKET, an RST is ECONNRESET).

async function fetchFailure(port) {
  try {
    await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(5000) });
  } catch (error) {
    return error;
  }
  return assert.fail("the request was expected to fail");
}

async function failureFromServer(onConnection) {
  const server = net.createServer(onConnection);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    return await fetchFailure(server.address().port);
  } finally {
    server.close();
  }
}

test("a port that accepts and then resets is reported as the forward, not as a down service", async () => {
  for (const [shape, drop] of [["close", (s) => s.destroy()], ["RST", (s) => s.resetAndDestroy()]]) {
    const { recorded, deps } = harness({ health: null, error: await failureFromServer(drop) });
    await checkService(deps);
    assert.equal(recorded[0][2], "port-forward-reset", `a ${shape} after accept read as a down service`);
  }
});

test("CONTROL: a closed port is still a service that is not there", async () => {
  const probe = net.createServer();
  await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
  const { port } = probe.address();
  await new Promise((resolve) => probe.close(resolve));
  const { recorded, deps } = harness({ health: null, error: await fetchFailure(port) });
  await checkService(deps);
  assert.equal(recorded[0][2], "unreachable");
});
