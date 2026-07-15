function normalizedSessionMode(value) {
  return String(value || "").trim().toLowerCase();
}

const LIVE_MANAGED_STATUSES = new Set(["online", "working", "blocked"]);

export function localAgentNeedsDispatchHosting({ agentId = "", channelsEnabled = false } = {}) {
  return !(String(agentId || "").trim() && channelsEnabled === true);
}

export function managedAgentNeedsDispatchHosting(agent = {}) {
  if (normalizedSessionMode(agent?.sessionMode) !== "managed") return false;
  const status = String(agent?.statusRaw || agent?.status || "").trim().toLowerCase();
  return LIVE_MANAGED_STATUSES.has(status);
}

export function reconcileManagedStateWithSnapshot(remoteAgentState, agentsById = {}) {
  const removed = [];

  for (const [agentId, state] of remoteAgentState.entries()) {
    if (normalizedSessionMode(state?.info?.sessionMode) !== "managed") continue;

    const current = agentsById?.[agentId];
    if (!current) continue;
    if (normalizedSessionMode(current.sessionMode) === "managed") continue;

    remoteAgentState.delete(agentId);
    removed.push(agentId);
  }

  return removed;
}

export async function resolveFreshManagedTeardownTargets({ selfBridgeId, fetchOwnership }) {
  try {
    const records = await fetchOwnership();
    const agentIds = [];
    const seen = new Set();

    for (const record of Array.isArray(records) ? records : []) {
      const agentId = String(record?.agentId || "").trim();
      const owningBridgeId = String(record?.owningBridgeId || "").trim();
      if (!agentId || owningBridgeId !== selfBridgeId || seen.has(agentId)) continue;
      seen.add(agentId);
      agentIds.push(agentId);
    }

    return { agentIds, source: "fresh-ownership" };
  } catch (error) {
    return {
      agentIds: [],
      skipped: "ownership-unavailable",
      error: error instanceof Error ? error : new Error(String(error)),
    };
  }
}

export async function bootstrapManagedEnvironmentBridge({
  registerEnvironment,
  sweepSurvivors,
  sweepTombstones = async () => {},
  syncManagedAgents,
  startSpawnLoop,
} = {}) {
  const registered = await registerEnvironment?.();
  if (!registered) {
    return { started: false, skipped: "registration-unavailable" };
  }

  const swept = await sweepSurvivors?.();
  if (swept === false) {
    return { started: false, skipped: "survivor-sweep-unavailable" };
  }

  await sweepTombstones();
  await syncManagedAgents?.();
  startSpawnLoop?.();
  return { started: true };
}
