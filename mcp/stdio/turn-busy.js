export function agentHeartbeatPayload({
  bridgeId = "",
  machineId = "",
  turnBusy,
  turnRunId = "",
  turnRuntime = "",
} = {}) {
  const body = {
    bridgeId: String(bridgeId || ""),
    machineId: String(machineId || ""),
  };
  if (typeof turnBusy === "boolean") {
    body.turnBusy = turnBusy;
    const runId = String(turnRunId || "").trim();
    const runtime = String(turnRuntime || "").trim();
    if (runId) body.turnRunId = runId;
    if (runtime) body.turnRuntime = runtime;
  }
  return body;
}

export function activeTurnHeartbeatPayload({ bridgeId = "", machineId = "", activeRun = {} } = {}) {
  return agentHeartbeatPayload({
    bridgeId,
    machineId,
    turnBusy: true,
    turnRunId: activeRun.runId || activeRun.id || "",
    turnRuntime: activeRun.runtime || "",
  });
}
