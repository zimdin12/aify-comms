// Only the bridge that OWNS an agent writes the field that names its owner.
//
// MEASURED ON THE OPERATOR'S HOST, 2026-08-29, on a live and perfectly healthy agent:
//
//     live environment bridge          e720826b-741c-455f-b8bd-e4659777e0c7
//     comms-senior-dev, 03:44          cd17b4c8-ebdb-4980-9492-b2a69435b590
//     comms-senior-dev, minutes later  a0897fff-a6c4-4c99-8349-8b9d750dd22a
//     ef-manager (just spawned)        e720826b-741c-455f-b8bd-e4659777e0c7
//
// Two different ids for one agent within minutes, neither the environment bridge's, while an agent the
// environment bridge had just spawned read correctly. `runtimeState.bridgeInstanceId` had FOUR writers
// with TWO meanings: `spawn-loop.mjs` and `managed-environment-sync.mjs` write the environment bridge
// that hosts the delivery loop, while `auto-registration.mjs` and `registration-tool.mjs` write
// whichever process is registering -- for a managed agent, its own per-session sidecar.
//
// WHAT IT COST. `aify-comms doctor` reported that agent as an orphaned managed delivery loop "bound to
// no live bridge, so they hold a gateway and a session that nothing can address", while the agent was
// answering messages. Its remedy: "Restart each named agent -- or relaunch the environment bridge,
// whose boot survivor sweep collects them all." Relaunching the environment bridge reaps the managed
// fleet. A false alarm whose fix is destructive is worse than no alarm at all.
//
// `managed-ownership.mjs` reads the same field to decide `ownerLive`, so the wrong answer reaches
// teardown decisions and not only a report.
import assert from "node:assert/strict";
import { test } from "node:test";

import { mayClaimEnvironmentOwnership } from "../environment-ownership-claim.mjs";

test("THE DEFECT: a managed agent's own sidecar does not claim ownership", () => {
  const verdict = mayClaimEnvironmentOwnership({ sessionMode: "managed" });
  assert.equal(verdict.claim, false);
  assert.match(verdict.reason, /environment bridge/);
});

test("nor does a managed WRAPPER CHILD, whatever mode it thinks it resolved", () => {
  // The belt to the mode's braces. `AIFY_MANAGED_VIA_WRAPPER=1` is set by the launcher that started
  // this process, and it is true regardless of how session-mode resolution went inside -- which
  // matters because that resolution has its own history of falling toward "resident".
  assert.equal(
    mayClaimEnvironmentOwnership({ sessionMode: "resident", managedWrapperChild: true }).claim,
    false,
  );
});

test("THE ENVIRONMENT BRIDGE IS NOT A CASE HERE, and that is the design", () => {
  // It takes no `isEnvironmentBridge` flag. Both callers are an agent's own session, and
  // `auto-registration.mjs` returns before them when the process IS the environment bridge; the two
  // correct writers -- spawn-loop and managed-environment-sync -- are the environment bridge by
  // construction and write unconditionally.
  //
  // The alternative was one decision point for all four writers, and a gate in this suite refused it
  // by name: routing them through here adds two more references to the environment-bridge marker
  // Phase 8 is retiring. Asserted so the flag is not quietly reintroduced.
  assert.equal(
    mayClaimEnvironmentOwnership({ sessionMode: "managed", isEnvironmentBridge: true }).claim, false,
    "an ignored argument came back to life; the environment bridge must not be a case this decides",
  );
});

test("a resident session's own bridge still writes it", () => {
  // Not a regression to tolerate: for a resident agent the field genuinely means its own MCP bridge,
  // and `hermes-managed-host.js` says so in its own comment when explaining why it must NOT send a
  // bridge id of its own.
  assert.equal(mayClaimEnvironmentOwnership({ sessionMode: "resident" }).claim, true);
});

test("an unrecognised mode CLAIMS, and that is the deliberate direction", () => {
  // The one place here where permissive is safe. A resident whose mode string drifted and stopped
  // writing the field would leave nothing naming its owner, and every reader fails closed on empty.
  // Managed is the case that must be refused, and managed is the case positively identified.
  assert.equal(mayClaimEnvironmentOwnership({ sessionMode: "something-new" }).claim, true);
  assert.equal(mayClaimEnvironmentOwnership().claim, true);
});

test("case and whitespace do not decide ownership", () => {
  // A guard that a capital letter walks past is decoration, and this repo has shipped exactly that:
  // `9d9e2914 fix(launch-mode): the STOP marker was compared case-sensitively, on both sides of the
  // wire`.
  for (const mode of ["Managed", " MANAGED ", "mAnAgEd"]) {
    assert.equal(mayClaimEnvironmentOwnership({ sessionMode: mode }).claim, false, mode);
  }
});

test("every verdict says why, including the ones that allow", () => {
  // A reason present only on refusal teaches nobody why the field IS set on the paths where it is,
  // and the next person to read this file will be someone wondering why their agent has no owner.
  for (const input of [{ sessionMode: "managed" }, { sessionMode: "resident" }, { isEnvironmentBridge: true }]) {
    assert.ok(mayClaimEnvironmentOwnership(input).reason.length > 10, JSON.stringify(input));
  }
});
