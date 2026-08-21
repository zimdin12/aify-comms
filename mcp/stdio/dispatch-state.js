const TERMINAL_RUN_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "expired",
  "answered",
  "operator_closed",
]);

function normalizeId(value) {
  return String(value || "").trim();
}

function normalizeStatus(value) {
  return normalizeId(value).toLowerCase();
}

export function shouldDropLocalActiveRun(activeRun, backendRun, { bridgeId = "", agentId = "" } = {}) {
  const localRunId = normalizeId(activeRun?.runId || activeRun?.id);
  if (!localRunId) return { drop: false, reason: "inactive" };
  if (!backendRun) return { drop: true, reason: "backend_missing" };

  const backendRunId = normalizeId(backendRun.id || backendRun.runId);
  const backendStatus = normalizeStatus(backendRun.status);
  if (backendRunId && backendRunId !== localRunId) return { drop: true, reason: "backend_not_owned" };
  if (TERMINAL_RUN_STATUSES.has(backendStatus)) return { drop: true, reason: "backend_terminal" };

  const expectedAgentId = normalizeId(agentId);
  const backendAgentId = normalizeId(backendRun.targetAgentId || backendRun.targetAgent || backendRun.target_agent || backendRun.agentId);
  if (expectedAgentId && backendAgentId && backendAgentId !== expectedAgentId) {
    return { drop: true, reason: "backend_not_owned" };
  }

  const expectedBridgeId = normalizeId(bridgeId);
  const backendBridgeId = normalizeId(backendRun.claimBridgeId || backendRun.bridgeId || backendRun.bridge_id || backendRun.ownerBridgeId || backendRun.claimedByBridgeId);
  if (expectedBridgeId && backendBridgeId && backendBridgeId !== expectedBridgeId) {
    return { drop: true, reason: "backend_not_owned" };
  }

  return { drop: false, reason: "active" };
}
