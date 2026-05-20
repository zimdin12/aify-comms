import assert from "node:assert/strict";
import {
  shouldDropLocalActiveRun,
} from "../dispatch-state.js";

assert.deepEqual(
  shouldDropLocalActiveRun({ runId: "run-1" }, null, { bridgeId: "bridge-1", agentId: "agent-1" }),
  { drop: true, reason: "backend_missing" },
  "missing backend run should clear stale local ACTIVE_RUNS so queued work can be claimed",
);

for (const status of ["completed", "failed", "cancelled", "expired", "answered", "operator_closed"]) {
  assert.deepEqual(
    shouldDropLocalActiveRun({ runId: "run-1" }, { id: "run-1", status, targetAgent: "agent-1", bridgeId: "bridge-1" }, { bridgeId: "bridge-1", agentId: "agent-1" }),
    { drop: true, reason: "backend_terminal" },
    `backend terminal status ${status} should clear stale local ACTIVE_RUNS`,
  );
}

assert.deepEqual(
  shouldDropLocalActiveRun({ runId: "run-1" }, { id: "run-1", status: "running", targetAgent: "agent-2", bridgeId: "bridge-1" }, { bridgeId: "bridge-1", agentId: "agent-1" }),
  { drop: true, reason: "backend_not_owned" },
  "backend run for a different agent should clear local active state",
);

assert.deepEqual(
  shouldDropLocalActiveRun({ runId: "run-1" }, { id: "run-1", status: "running", targetAgentId: "agent-2", claimBridgeId: "bridge-1" }, { bridgeId: "bridge-1", agentId: "agent-1" }),
  { drop: true, reason: "backend_not_owned" },
  "serialized backend targetAgentId mismatch should clear local active state",
);

assert.deepEqual(
  shouldDropLocalActiveRun({ runId: "run-1" }, { id: "run-1", status: "running", targetAgent: "agent-1", bridgeId: "bridge-2" }, { bridgeId: "bridge-1", agentId: "agent-1" }),
  { drop: true, reason: "backend_not_owned" },
  "backend run owned by another bridge should clear local active state",
);

assert.deepEqual(
  shouldDropLocalActiveRun({ runId: "run-1" }, { id: "run-1", status: "running", targetAgent: "agent-1", bridgeId: "bridge-1" }, { bridgeId: "bridge-1", agentId: "agent-1" }),
  { drop: false, reason: "active" },
  "matching nonterminal backend run should keep local active state",
);


console.log("dispatch-state.test.js: all assertions passed");
