// An orphaned managed delivery loop is a running process nothing can address, and the control plane
// cannot see it.
//
// THE FIELD CASE, 2026-08-26. Six `hermes-managed-host.js run <agent>` loops were alive on the
// operator's host, the oldest at 96 minutes, each holding a hermes gateway. Every one of their agents
// read `available` on the dashboard -- because `available` means "no live channel sidecar", which was
// true, while the process itself was running and heartbeating. The operator's own words were that
// agents "seem to be running still" after the panel reported them dead. Both readings were correct;
// they were about different processes.
//
// WHY NOTHING COLLECTS THEM: the loop is launched detached under `nohup`, deliberately, so it
// outlives its launcher. The survivor sweep that would reap it runs at bridge BOOT, so a loop
// orphaned mid-session accumulates until the next relaunch.
//
// AND WHY THIS ONLY REPORTS: deciding to kill needs ownership this predicate does not have. The repo
// already has one env-scoped reaper that does it at the only moment it is safe.
import assert from "node:assert/strict";
import test from "node:test";

import { managedOrphanVerdict } from "../doctor-predicates.js";

const LIVE = "5fdddb0f-489b-4a05-aecd-bd4d14f07ccb";
const DEAD = "be696562-61cd-448b-9d7d-2c87419a4f32";

const bound = (bridgeInstanceId) => ({ sessionMode: "managed", runtimeState: { bridgeInstanceId } });

test("a loop bound to the live bridge is the live worker, not an orphan", () => {
  const verdict = managedOrphanVerdict({
    loops: [{ agentId: "comms-senior-dev", pid: 193348 }],
    agents: { "comms-senior-dev": bound(LIVE) },
    liveBridgeId: LIVE,
  });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "ok");
});

test("a loop bound to a bridge that is gone is an orphan, and it is named", () => {
  // The real shape: graph-senior-dev's `bridgeInstanceId` was a bridge instance that no longer
  // existed, while its `lastSeen` refreshed every few seconds because the orphan was heartbeating.
  const verdict = managedOrphanVerdict({
    loops: [{ agentId: "graph-senior-dev", pid: 185120 }],
    agents: { "graph-senior-dev": bound(DEAD) },
    liveBridgeId: LIVE,
  });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "orphaned");
  assert.match(verdict.detail, /graph-senior-dev/);
  assert.match(verdict.detail, /185120/);
  assert.ok(verdict.fix, "an orphan the operator cannot act on is a complaint, not a check");
});

test("a loop for an agent the service does not know is an orphan too", () => {
  // Nothing can address it: there is no row to route a dispatch through.
  const verdict = managedOrphanVerdict({
    loops: [{ agentId: "long-deleted-agent", pid: 4242 }],
    agents: {},
    liveBridgeId: LIVE,
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /long-deleted-agent/);
});

test("the nohup parent and its node child are ONE loop, not two", () => {
  // Enumeration matches the command line, and the launcher is `nohup node hermes-managed-host.js run
  // <agent>` -- so both processes match. Counting pids reports every loop twice, which would turn a
  // clean host into a doubled orphan count.
  const verdict = managedOrphanVerdict({
    loops: [
      { agentId: "graph-senior-dev", pid: 18100 },   // nohup.exe
      { agentId: "graph-senior-dev", pid: 185120 },  // its node child
    ],
    agents: { "graph-senior-dev": bound(DEAD) },
    liveBridgeId: LIVE,
  });
  assert.match(verdict.detail, /^1 of 1 managed delivery loop/);
  assert.match(verdict.detail, /pid 18100\+185120/, "both pids belong to the one loop it names");
});

test("no loops at all is clean", () => {
  const verdict = managedOrphanVerdict({ loops: [], agents: {}, liveBridgeId: LIVE });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "none");
});

test("orphans and live workers are counted apart", () => {
  const verdict = managedOrphanVerdict({
    loops: [
      { agentId: "comms-senior-dev", pid: 1 },
      { agentId: "graph-senior-dev", pid: 2 },
      { agentId: "mc-senior-dev", pid: 3 },
    ],
    agents: {
      "comms-senior-dev": bound(LIVE),
      "graph-senior-dev": bound(DEAD),
      "mc-senior-dev": bound(DEAD),
    },
    liveBridgeId: LIVE,
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /^2 of 3 managed delivery loop/);
  assert.doesNotMatch(verdict.detail, /comms-senior-dev/, "the live worker is not accused");
});

// ── no evidence is not a pass ────────────────────────────────────────────────────────────────
// `env-bridge` and `bridge-current` each shipped green-by-default and each was wrong the same way
// (`756f3a5`, `a2f9e42`). A check that gathered nothing must not read like a check that found
// nothing.

test("an unreadable process table is unknown, never clean", () => {
  const verdict = managedOrphanVerdict({ loops: null, agents: {}, liveBridgeId: LIVE });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /process table/);
});

test("a service that did not answer is unknown, never clean", () => {
  const verdict = managedOrphanVerdict({ loops: [], agents: null, liveBridgeId: LIVE });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /service/);
});

test("no bridge online is unknown, never clean", () => {
  // There is no id to compare against, so EVERY loop would look orphaned. Reporting them all would be
  // a confident wrong answer, which is worse than admitting there is nothing to compare to.
  const verdict = managedOrphanVerdict({
    loops: [{ agentId: "graph-senior-dev", pid: 185120 }],
    agents: { "graph-senior-dev": bound(DEAD) },
    liveBridgeId: null,
  });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /no environment bridge is online/);
});

test("every missing input is named, not just the first", () => {
  const verdict = managedOrphanVerdict({ loops: null, agents: null, liveBridgeId: null });
  assert.match(verdict.detail, /process table/);
  assert.match(verdict.detail, /service/);
  assert.match(verdict.detail, /environment bridge/);
});

test("called with nothing at all, it is unknown rather than throwing", () => {
  // doctor calls every check; one that throws takes the whole report with it.
  const verdict = managedOrphanVerdict();
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
});
