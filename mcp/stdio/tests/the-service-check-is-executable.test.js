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
import { test } from "node:test";

import { checkService } from "../service-check.mjs";

/** A recorder for `add`, plus canned service answers. Nothing here touches a network or a checkout. */
function harness({ health = { status: "healthy" }, version = {}, repo = null } = {}) {
  const recorded = [];
  return {
    recorded,
    deps: {
      get: async (path) => (path === "/health" ? health : version),
      add: (...args) => { recorded.push(args); return args; },
      sh: () => "0",
      repo,
      serverUrl: "http://127.0.0.2:1",
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
