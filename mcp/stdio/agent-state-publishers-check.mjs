// The `agent-state-publishers` doctor row: whether the aify-env publishers this service builds every status word from
// are reporting. Since the 0.9.8 switch an agent's word is its aify-env's published record, so a service with no
// fresh publisher reads every agent `unknown`, and a publisher that says it cannot read its own state hides its agents.
// It replaced the `agent-state-shadow` row (0.9.9 S1), which counted the switch's comparison and was never ok.
//
// A STALE OLD KEY IS NAMED, NOT FAILED. A dead herdr-aify daemon's key stays until it is retired, and it yields to a
// live publisher of the same agents (task #5), so it changes no word.

const SAYS = { unavailable: "cannot read their own state", lost: "lost by this service until their next snapshot" };
const FAILING = new Set(Object.keys(SAYS));

/** PURE. The row over a `/api/v1/agent-state/publishers` answer, or null when there was none. */
export function publishersVerdict(answer) {
  if (!answer || typeof answer !== "object" || !Array.isArray(answer.publishers)) {
    return { ok: false, code: "unknown", detail: "the service did not answer /api/v1/agent-state/publishers" };
  }
  if (answer.readable === false) {
    return { ok: false, code: "lost", detail: "the service lost its mirror of aify-env's records: every agent reads unknown until it restarts" };
  }
  const rows = answer.publishers.filter((p) => p && typeof p.availability === "string");
  const name = (p) => `${p.machineId}/${p.instance} (${p.availability}, ${Number(p.agents) || 0} agents)`;
  const fresh = rows.filter((p) => p.availability === "fresh");
  const failing = rows.filter((p) => FAILING.has(p.availability));
  const others = rows.filter((p) => p.availability !== "fresh" && !FAILING.has(p.availability));
  const parts = [`${fresh.length} of ${rows.length} publisher(s) fresh`];
  for (const [availability, says] of Object.entries(SAYS)) {
    const these = failing.filter((p) => p.availability === availability);
    if (these.length) parts.push(`${says}: ${these.map(name).join(", ")}`);
  }
  if (others.length) parts.push(`not reporting fresh: ${others.map(name).join(", ")}`);
  const detail = parts.join("; ");
  if (failing.length) return { ok: false, code: "publisher-unavailable", detail };
  if (!fresh.length) return { ok: false, code: "no-fresh-publisher", detail };
  return { ok: true, code: "fresh", detail };
}

/** One read through the doctor's authenticated GET. */
export async function checkAgentStatePublishers({ get, add }) {
  let answer = null;
  try { answer = await get("/api/v1/agent-state/publishers"); } catch { /* the verdict says the service did not answer */ }
  const verdict = publishersVerdict(answer);
  return add("agent-state-publishers", verdict.ok, verdict.code, verdict.detail,
    verdict.ok ? undefined : "Run `aify-env doctor` on the host whose publisher is named: a daemon that cannot read its own "
      + "state says why there, and a host with no running aify-env publishes nothing.");
}
