export function agentHeartbeatPayload({
  bridgeId = "",
  machineId = "",
  terminalId = "",
  turnBusy,
  turnRunId = "",
  turnRuntime = "",
} = {}) {
  const body = {
    bridgeId: String(bridgeId || ""),
    machineId: String(machineId || ""),
  };
  const terminal = String(terminalId || "").trim();
  if (terminal) body.terminalId = terminal;
  if (typeof turnBusy === "boolean") {
    body.turnBusy = turnBusy;
    const runId = String(turnRunId || "").trim();
    const runtime = String(turnRuntime || "").trim();
    if (runId) body.turnRunId = runId;
    if (runtime) body.turnRuntime = runtime;
  }
  return body;
}

export function activeTurnHeartbeatPayload({ bridgeId = "", machineId = "", terminalId = "", activeRun = {} } = {}) {
  return agentHeartbeatPayload({
    bridgeId,
    machineId,
    terminalId,
    turnBusy: true,
    turnRunId: activeRun.runId || activeRun.id || "",
    turnRuntime: activeRun.runtime || "",
  });
}
