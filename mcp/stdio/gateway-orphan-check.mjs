// The `gateway-orphans` check: hermes gateway hosts still running for agents that have no worker.
//
// THE INCIDENT, 2026-08-31. The operator killed aify-env with managed agents live. Their delivery
// loops went with it; their per-agent GATEWAY HOSTS did not. Nothing collected them, because the
// survivor sweep runs at bridge BOOT and on GRACEFUL shutdown -- and an abrupt kill is neither. An
// hour later `hermes update` refused to run:
//
//     Other Hermes processes are running from this install's venv: ... and 39 more
//     On Windows these keep native extension files (.pyd) locked, so the dependency
//     update would fail partway and leave a broken install.
//
// 45 processes. The operator asked whether they were orphans from aify-env, and the answer took a
// port range and a marker file to establish, because nothing reported it.
//
// `managed-orphans` is this check's sibling and watches the DELIVERY LOOPS. It would have said
// nothing here: the loops were the half that died correctly. The gateways are the half that survives,
// and until now they had no watcher at all.
//
// HOW LONG THEY ACTUALLY SURVIVE, measured from hermes' own source rather than guessed. A gateway
// WHAT IS ESTABLISHED, AND WHAT IS NOT. This header went through three wrong explanations before it
// got here, so the two are kept apart deliberately.
//
// ESTABLISHED, by reading hermes' source: `_session_is_evictable` returns False while
// `session["running"]` is set, so a MID-TURN session is exempt from the idle reaper. That reaper runs
// as a daemon thread started at module scope inside the GATEWAY process, so killing whatever spawned
// the gateway does not stop it. Its scan period is `_REAPER_SCAN_S` = 300s and a client-less session
// becomes eligible after `_WS_ORPHAN_REAP_GRACE_S`, 20s by default and unset on this host.
//
// NOT ESTABLISHED: why those 45 processes exited. The reaper evicts SESSIONS; nothing found in that
// server exits the PROCESS when its last session goes, and the six still alive when this was
// investigated had been CREATED in the same minute as the operator's `hermes update`, not survived
// from the kill twenty minutes earlier. So the population was moving, not merely draining, and the
// cause is unknown. Three explanations were offered and retracted before that was admitted.
//
// THIS CHECK THEREFORE MAKES NO CAUSAL CLAIM. It reports STATE: which hermes gateways are running in
// aify-comms' own port range, which agent each belongs to, and whether anything is behind them. That
// is a question an operator cannot answer from anywhere else -- establishing it by hand took a port
// range, a marker directory and a process walk -- and it stays true regardless of which mechanism
// eventually explains the lifetimes.
//
// REPORTS, NEVER KILLS -- the same ruling as `managed-orphans`. A gateway is a live process holding a
// visible TUI; deciding it is unwanted is the operator's call, and a doctor that reaps on its own
// judgement is one bad inference away from taking a session someone is reading.

/** Gateways whose port falls in aify-comms' per-agent range, keyed by port. */
export function gatewaysInRange(rows, { toPort, base, span }) {
  const found = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    const port = toPort(row && row.commandLine);
    if (port === null || port === undefined) continue;
    if (port < base || port >= base + span) continue;
    found.push({ pid: row.pid, port });
  }
  return found;
}

/**
 * Which agent owns each gateway, from the port markers on disk.
 *
 * THE MARKER IS THE ONLY LINK. A gateway's command line carries a port and no agent id, and the
 * `aify-hermes-port-<agent>` file is what binds the two. A port with no marker is reported as
 * unowned rather than dropped: an unclaimed gateway is MORE suspicious than a claimed one, and
 * silently discarding it would hide exactly the process nobody can account for.
 */
export function gatewayOwners(portMarkers) {
  const byPort = new Map();
  for (const [agentId, port] of Object.entries(portMarkers || {})) {
    // `Number("")` and `Number(null)` are both 0, and 0 is an integer -- so a marker file that exists
    // but is EMPTY would otherwise register as "this agent owns port 0" and quietly claim any gateway
    // the parser also failed on. An empty marker was sitting in the operator's TEMP when this was
    // written, so it is a real input rather than a defensive flourish.
    const n = Number(String(port ?? "").trim());
    if (Number.isInteger(n) && n > 0 && n < 65536) byPort.set(n, agentId);
  }
  return byPort;
}

/**
 * What the running gateways mean.
 *
 * SCOPED TO MANAGED AGENTS, and that scope is the difference between a useful row and a nuisance. A
 * RESIDENT hermes session legitimately runs its own gateway with no managed delivery loop behind it;
 * counting those would report the operator's own terminal as an orphan every single run.
 *
 * NO EVIDENCE IS NOT A PASS, the same three ways as `managed-orphans`: an unreadable process table,
 * a service that did not answer, and no readable markers each make the answer unknown rather than
 * clean.
 */
export function gatewayOrphanVerdict({ gateways = null, owners = null, loopAgentIds = null, agents = null } = {}) {
  const missing = [];
  if (gateways === null) missing.push("the process table could not be read");
  if (owners === null) missing.push("the gateway port markers could not be read");
  if (loopAgentIds === null) missing.push("the delivery loops could not be enumerated");
  if (agents === null) missing.push("the service did not answer");
  if (missing.length) {
    return {
      ok: false,
      code: "unknown-all",
      detail: `Orphaned gateway hosts could not be counted: ${missing.join("; ")}. Nothing was `
        + "verified, so this is not a clean result.",
      fix: "Fix the named condition and re-run; `aify-comms doctor` reports each separately.",
    };
  }

  if (!gateways.length) {
    return { ok: true, code: "none", detail: "no hermes gateway host is running in the managed port range" };
  }

  const live = new Set(loopAgentIds);
  const orphans = [];
  for (const gw of gateways) {
    const agentId = owners.get(gw.port);
    if (!agentId) {
      // Unclaimed: no marker names this port. Nothing can say whose it is, which is worse than an
      // identified orphan, not better.
      orphans.push({ ...gw, agentId: "", why: "no port marker claims it" });
      continue;
    }
    if (live.has(agentId)) continue;
    const agent = agents[agentId];
    // A resident session owns its gateway legitimately and has no managed loop by design.
    if (agent && agent.sessionMode !== "managed") continue;
    orphans.push({ ...gw, agentId, why: agent ? "managed agent with no delivery loop" : "no such agent" });
  }

  if (!orphans.length) {
    return {
      ok: true,
      code: "ok",
      detail: `${gateways.length} gateway host(s) running, each with a live delivery loop or a resident owner`,
    };
  }

  const named = orphans.map((o) => `${o.agentId || "(unclaimed)"} pid ${o.pid} port ${o.port}`);
  return {
    ok: false,
    code: "orphaned",
    detail: `${orphans.length} of ${gateways.length} hermes gateway host(s) have no worker behind them: `
      + named.join(", "),
    fix: "On Windows these hold hermes' native `.pyd` files locked, which makes `hermes update` refuse "
      + "to run and list them. aify-comms' own survivor sweep runs at bridge BOOT and on GRACEFUL "
      + "shutdown, so an abrupt kill is not covered by it; hermes has an idle reaper of its own, but "
      + "it exempts any session that is mid-turn and how long these processes actually live has NOT "
      + "been established. Stop one with `hermes dashboard stop` or by pid once you have confirmed "
      + "nobody is reading that TUI. Reported rather than reaped: a live gateway may be a session "
      + "someone is watching, and this row says what is running, not that it is unwanted.",
  };
}

/**
 * Enumerate the gateways, name their owners, and say which have nothing behind them.
 */
export async function checkGatewayOrphans({ get, add, listProcesses, toPort, readPortMarkers, loopAgent, base, span }) {
  let gateways = null;
  let loopAgentIds = null;
  try {
    const rows = listProcesses();
    // AN EMPTY PROCESS TABLE IS NOT AN EMPTY ANSWER -- the same conflation that hid a broken default
    // for a whole release. This process is running, so a table without it did not read the host.
    if (!rows.some((row) => row && row.pid === process.pid)) throw new Error("enumeration did not include this process");
    gateways = gatewaysInRange(rows, { toPort, base, span });
    loopAgentIds = rows.map((row) => loopAgent(row && row.commandLine)).filter(Boolean);
  } catch {
    gateways = null;
    loopAgentIds = null;
  }

  let owners = null;
  try {
    owners = gatewayOwners(readPortMarkers());
  } catch {
    owners = null;
  }

  const body = await get("/api/v1/agents");
  const agents = body && body.agents && typeof body.agents === "object" ? body.agents : null;

  const verdict = gatewayOrphanVerdict({ gateways, owners, loopAgentIds, agents });
  return add("gateway-orphans", verdict.ok, verdict.code, verdict.detail, verdict.fix);
}
