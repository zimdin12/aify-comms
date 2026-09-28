export function agentHeartbeatPayload({
  bridgeId = "",
  machineId = "",
  terminalId = "",
  turnBusy,
  turnRunId = "",
  turnRuntime = "",
  firedAtUs,
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
    // When this bridge observed the turn it reports (turn-event-stamp.mjs). A beat that starts a turn is
    // ordered against the turn's ends by it, so an end observed earlier cannot close it (0.7.6 review, O2).
    if (Number.isSafeInteger(firedAtUs) && firedAtUs > 0) body.firedAtUs = firedAtUs;
  }
  return body;
}

export function activeTurnHeartbeatPayload({
  bridgeId = "", machineId = "", terminalId = "", activeRun = {}, firedAtUs,
} = {}) {
  return agentHeartbeatPayload({
    bridgeId,
    machineId,
    terminalId,
    turnBusy: true,
    turnRunId: activeRun.runId || activeRun.id || "",
    turnRuntime: activeRun.runtime || "",
    firedAtUs,
  });
}
