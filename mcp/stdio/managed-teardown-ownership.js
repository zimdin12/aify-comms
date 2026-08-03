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

// `lastKnownOwnedAgentIds` (2026-08-03) is the fallback for the case that leaked in practice.
//
// The fresh-ownership read is the right primary source and the fail-safe is the right default:
// never reap from a stale cache, because a managed->resident switch could make us kill a resident
// the operator is using. But the read goes to the SERVICE, and on a full shutdown the service is
// usually already gone — so teardown resolved "ownership-unavailable", reaped nothing, and left
// the note "the next boot sweep handles genuine managed survivors". On a full shutdown there is no
// next boot. Observed live: nine hermes processes (three gateway-host triads) survived the
// operator killing every hermes they had open, the oldest by two days, none listening, holding
// ~880MB.
//
// So when the live read fails we fall back to what THIS bridge instance already PROVED it owned on
// an earlier successful read in this same process. That is evidence, not a guess, and it is
// strictly narrower than the fresh read would have been. The residual risk — an agent that
// switched managed->resident after our last successful read AND during shutdown — is bounded by
// what teardown actually targets: the managed triad enumerated from this bridge's own markers and
// a process scan scoped to its cwdRoots. A resident agent has no such triad owned by this bridge.
//
// With no prior successful read there is nothing proven, so the original fail-safe still applies.
export async function resolveFreshManagedTeardownTargets({
  selfBridgeId,
  fetchOwnership,
  lastKnownOwnedAgentIds = null,
}) {
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
    const proven = Array.isArray(lastKnownOwnedAgentIds)
      ? lastKnownOwnedAgentIds.map((id) => String(id || "").trim()).filter(Boolean)
      : [];
    if (proven.length) {
      return {
        agentIds: [...new Set(proven)],
        source: "last-known-ownership",
        degraded: true,
        error: error instanceof Error ? error : new Error(String(error)),
      };
    }
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
