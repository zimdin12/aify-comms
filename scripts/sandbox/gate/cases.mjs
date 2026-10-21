// The frozen cases of plan s4-hard-switch page 2, as scenarios over a Pair (pair.mjs). Each scenario returns its
// `marks`; the judges (judges.mjs) decide. `expected` and `red` are page 2's words, frozen here before any run.
// Several cases share one scenario where page 2 chains them (paused, then resumed); each keeps its own judge.

import { randomUUID } from "node:crypto";
import { sleep } from "../env-against-comms.mjs";
import { AGENT, Pair } from "./pair.mjs";
import { ms } from "./judges.mjs";

const WINDOW_MS = 20_000; // how long a withheld run is watched, against a claimer polling every ~2 s
const SILENCE_MS = 185_000; // just past decision 2's 180 s, on comms' clock
// A watcher snapshot taken while comms restarted carries only an error; every helper reads it as holding nothing.
const run = (seen, id) => seen?.runs?.find((r) => r.id === id) ?? null;
const record = (seen) => seen?.records?.find((r) => r.instance === "default") ?? null;
const publisher = (seen) => seen?.publishers?.find((p) => p.instance === "default") ?? null;

/** The first timeline snapshot from `fromT` matching `test`: when comms applied it, and when the watcher saw it. */
function accepted(timeline, test, fromT = 0) {
  const seen = timeline.find((s) => s.t >= fromT && test(s));
  return seen ? { at: ms(publisher(seen)?.data_applied_at), seenAt: seen.t } : null;
}

function sent(pair, subject, extra) {
  const { runId, answer } = pair.send(subject, extra);
  const seen = pair.snapshot();
  return { runId, answer, sentAt: ms(run(seen, runId)?.requested_at) ?? seen.t };
}

/** Watch a withheld run for WINDOW_MS; the window is on comms' clock. */
async function window(pair) {
  const from = pair.snapshot().t;
  await sleep(WINDOW_MS);
  return { from, to: pair.snapshot().t };
}

const claimed = (pair, runId, timeoutMs) => pair.waitFor(() => {
  const r = run(pair.snapshot(), runId);
  return r && r.status !== "queued";
}, timeoutMs, `${runId} claimed`).catch(() => null);

async function busy(pair) {
  pair.control({ turn: "busy" });
  return pair.waitRecord((r) => r.busy === true, 60_000, "aify-env's busy record");
}

/** Until comms has applied aify-env's next complete snapshot, so a restart lands at the start of an epoch. */
const nextSnapshot = (pair) => pair.waitPublication("snapshot", Pair.appliedAt(pair.snapshot()) ?? 0, 90_000);
const legacyTurn = (pair) => pair.api("POST", `/agents/${AGENT}/turn-start`, {
  firedAtUs: Date.now() * 1000, machineId: pair.api("GET", `/agents/${AGENT}`).json?.agent?.machineId ?? "" });

export const SCENARIOS = {
  async "legacy-busy"(pair) {
    await pair.warm();
    legacyTurn(pair);
    const pre = pair.snapshot();
    // Not steerable: a steerable send is delivered to a busy agent by either reader, so it cannot tell them apart.
    const s = sent(pair, "C1 legacy busy, env idle", { queueIfBusy: true, steer: false });
    await claimed(pair, s.runId, WINDOW_MS);
    return { runId: s.runId, sentAt: s.sentAt, preLegacyBusy: pre.legacy[0]?.turn_busy ?? 0, preEnvBusy: record(pre)?.busy };
  },
  async "legacy-idle"(pair, evidence) {
    await pair.warm();
    const pre = await busy(pair);
    const s = sent(pair, "C2 legacy idle, env busy", { queueIfBusy: true, steer: false });
    const holdWindow = await window(pair);
    const idleT = pair.snapshot().t;
    pair.control({ turn: "idle" });
    // The idle acceptance is waited for in its own right, so a run claimed early still leaves it in the evidence.
    await pair.waitRecord((r) => r.busy === false, 60_000, "aify-env's idle record").catch(() => null);
    await claimed(pair, s.runId, 30_000);
    const timeline = evidence().timeline;
    return { runId: s.runId, preLegacyBusy: pre.legacy[0]?.turn_busy ?? 0, preEnvBusy: record(pre)?.busy, holdWindow,
      idleAccepted: accepted(timeline, (x) => record(x)?.busy === false, idleT) };
  },
  async steer(pair, evidence) {
    await pair.warm();
    const pre = await busy(pair);
    const s = sent(pair, "C3 busy, steerable");
    await pair.waitFor(() => pair.standin().some((e) => e.runId === s.runId), WINDOW_MS, "receipt").catch(() => null);
    const got = pair.standin().find((e) => e.runId === s.runId && (e.event === "delivered" || e.event === "control"));
    const until = got?.t ?? pair.snapshot().t;
    return { runId: s.runId, sentAt: s.sentAt, preEnvBusy: record(pre)?.busy,
      idleSeenBeforeReceipt: evidence().timeline.some((x) => x.t >= s.sentAt && x.t <= until && record(x)?.busy === false) };
  },
  async "stale-second"(pair) {
    await pair.warm();
    const seen = pair.snapshot();
    const machineId = publisher(seen).machine_id;
    const body = { kind: "snapshot", machineId, instance: "gate-second", generation: Date.now(), incarnationId: randomUUID(),
      publication: 1, inputs: { operatorStop: "tracked" }, complete: true,
      agents: [{ agentId: AGENT, lifetime: randomUUID(), state: "idle", stateCause: "at-prompt", busy: false,
        process: { state: "running", verified: "yes", pid: 4242 }, turn: null }] };
    const posted = pair.api("POST", "/agent-state", body);
    if (posted.status !== 204) throw new Error(`the second holder was refused: ${posted.status} ${posted.text}`);
    const second = pair.snapshot().publishers.find((p) => p.instance === "gate-second");
    const secondAppliedAt = ms(second?.data_applied_at);
    await pair.waitFor(() => pair.snapshot().t - secondAppliedAt > SILENCE_MS, SILENCE_MS + 60_000, "the second holder to go stale");
    const pre = pair.snapshot();
    const s = sent(pair, "C4 stale second holder");
    return { runId: s.runId, sentAt: s.sentAt, secondAppliedAt, envAppliedAt: Pair.appliedAt(pre),
      envBusyAtSend: record(pre)?.busy, window: await window(pair) };
  },
  async paused(pair, evidence) {
    await pair.warm();
    await busy(pair);
    pair.pauseEnv();
    const envAppliedAt = Pair.appliedAt(pair.snapshot());
    await pair.waitFor(() => pair.snapshot().t - envAppliedAt > SILENCE_MS, SILENCE_MS + 60_000, "180 s of receipt silence");
    const word = pair.snapshot().word;
    const q = sent(pair, "C5 queueIfBusy while unknown", { queueIfBusy: true, steer: false });
    const d = sent(pair, "C7 default while unknown");
    const held = await window(pair);
    const resumeT = pair.snapshot().t;
    pair.resumeEnv();
    pair.control({ turn: "idle" });
    await pair.waitRecord((r) => r.busy === false, 120_000, "aify-env's idle record after the resume").catch(() => null);
    await claimed(pair, q.runId, 30_000);
    await claimed(pair, d.runId, 30_000);
    const timeline = evidence().timeline;
    return { word, envAppliedAt, sentAt: Math.min(q.sentAt, d.sentAt), queueRunId: q.runId, defaultRunId: d.runId,
      window: held, freshAccepted: accepted(timeline, (x) => ms(publisher(x)?.data_applied_at) > envAppliedAt, resumeT),
      idleAccepted: accepted(timeline, (x) => record(x)?.busy === false, resumeT) };
  },
  async "restart-idle"(pair, evidence) {
    await pair.warm();
    await nextSnapshot(pair);
    const restartedAt = await pair.restartComms();
    const s = sent(pair, "C8 restart while idle");
    const wordBefore = pair.snapshot().word;
    await pair.waitPublication("snapshot", restartedAt, 90_000).catch(() => null);
    await claimed(pair, s.runId, 30_000);
    const timeline = evidence().timeline;
    return { runId: s.runId, sentAt: s.sentAt, restartedAt, wordBefore,
      snapshotAccepted: accepted(timeline, (x) => publisher(x)?.kind === "snapshot"
        && ms(publisher(x)?.data_applied_at) > restartedAt) };
  },
  async "restart-busy"(pair, evidence) {
    await pair.warm();
    const pre = await busy(pair);
    await nextSnapshot(pair);
    const restartedAt = await pair.restartComms();
    const read = pair.snapshot();
    await pair.waitPublication("snapshot", restartedAt, 90_000).catch(() => null);
    const timeline = evidence().timeline;
    return { preEnvBusy: record(pre)?.busy, restartedAt, readAt: read.t, word: read.word,
      snapshotAccepted: accepted(timeline, (x) => publisher(x)?.kind === "snapshot"
        && ms(publisher(x)?.data_applied_at) > restartedAt) };
  },
  async "restart-changes"(pair) {
    await pair.warm();
    await nextSnapshot(pair);
    const restartedAt = await pair.restartComms();
    pair.control({ turn: "busy" });
    const changes = await pair.waitPublication("changes", restartedAt, 40_000).catch(() => null);
    pair.control({ turn: "idle" });
    const snapshot = await pair.waitPublication("snapshot", restartedAt, 90_000).catch(() => null);
    await sleep(3000);
    return { restartedAt, wordAtChanges: changes?.word ?? null, wordAfterSnapshot: pair.snapshot().word,
      changesAccepted: changes ? { at: Pair.appliedAt(changes) } : null,
      snapshotAccepted: snapshot ? { at: Pair.appliedAt(snapshot) } : null };
  },
  async cold(pair) {
    const s = sent(pair, "C11 cold start");
    await claimed(pair, s.runId, 180_000);
    // The new lifetime's record is waited for in its own right, so a claim that came first still leaves it in evidence.
    await pair.waitRecord((r) => r.lifetime === pair.lifetime(), 120_000, "the new lifetime's record").catch(() => null);
    return { runId: s.runId, sentAt: s.sentAt };
  },
  async running(pair) {
    await pair.warm();
    pair.control({ turn: "idle", runStatus: "running" });
    const s = sent(pair, "C12 a running run on an idle record");
    await pair.waitFor(() => run(pair.snapshot(), s.runId)?.status === "running", 60_000, "the run running")
      .catch(() => null);
    await sleep(3000);
    const seen = pair.snapshot();
    return { runId: s.runId, runStatus: run(seen, s.runId)?.status ?? null, record: record(seen), word: seen.word };
  },
  async stop(pair) {
    await pair.warm();
    const before = pair.lifetime();
    const stopped = pair.api("POST", `/agents/${AGENT}/control`, { action: "stop", from_agent: "dashboard" });
    if (!stopped.ok) throw new Error(`the stop was refused: ${stopped.status} ${stopped.text}`);
    await pair.waitRecord((r) => r.state === "stopped", 90_000, "aify-env's stopped record");
    await sleep(2000);
    const wordStopped = pair.snapshot().word;
    const spawnsBefore = pair.snapshot().spawns.length;
    const deliveredBefore = pair.standin().filter((e) => e.event === "delivered").length;
    const s = sent(pair, "C13 send while stopped");
    await sleep(WINDOW_MS);
    const seen = pair.snapshot();
    const queuedAtStart = seen.runs.filter((r) => r.status === "queued").map((r) => r.id);
    const spawnsAfterSend = seen.spawns.length - spawnsBefore;
    const deliveredWhileStopped = pair.standin().filter((e) => e.event === "delivered").length - deliveredBefore;
    const started = pair.api("POST", `/agents/${AGENT}/control`, { action: "start", from_agent: "dashboard" });
    if (!started.ok) throw new Error(`the start was refused: ${started.status} ${started.text}`);
    await pair.waitFor(() => { const l = pair.lifetime(); return l && l !== before; }, 120_000, "a new lifetime");
    const wordAtStart = pair.snapshot().word;
    // A fresh lifetime reads turn-unknown until aify-env sees a turn, so "online" is judged after its first one.
    await busy(pair);
    pair.control({ turn: "idle" });
    await pair.waitRecord((r) => r.busy === false && r.state === "idle", 60_000, "the new lifetime idle");
    for (const runId of queuedAtStart) await claimed(pair, runId, 30_000);
    await sleep(3000);
    return { wordStopped, sendStored: s.answer.json?.stored === true, runId: s.runId, spawnsAfterSend,
      deliveredWhileStopped, queuedAtStart, wordAtStart, wordAfter: pair.snapshot().word };
  },
};

/** Page 2's cases, in its order: the scenario that produces each one's marks, and its frozen words. */
export const CASES = [
  { id: "legacy-busy-env-idle", scenario: "legacy-busy", expected: "delivered at once",
    red: "the reader still takes the legacy table (it holds)" },
  { id: "legacy-idle-env-busy", scenario: "legacy-idle",
    expected: "held; delivered on the first claim pass after comms accepts the idle publication",
    red: "the reader takes the legacy table (it delivers mid-turn)" },
  { id: "busy-steerable", scenario: "steer", expected: "steered", red: "the carve-out is removed" },
  { id: "stale-second-holder", scenario: "stale-second", expected: "eligibility unknown: withheld, steer included",
    red: "stale holders are ignored (known false: delivered)" },
  { id: "env-paused", scenario: "paused",
    expected: "after 180 s of receipt silence: `unknown` with a stale last-known note; sends queue; claims withhold, steer included",
    red: "unknown read as false, or steer checked before eligibility (the steerable run is delivered)" },
  { id: "env-resumed", scenario: "paused",
    expected: "queued work drains on the first claim pass after comms accepts a fresh publication", red: "the queue never drains" },
  { id: "default-send-unknown", scenario: "paused", expected: "queued, not claimed",
    red: "the claim gate withholds only `queueIfBusy` runs" },
  { id: "restart-idle", scenario: "restart-idle",
    expected: "before aify-env's next complete snapshot: `restored`, eligibility unknown, a send queues; after it: delivered",
    red: "restored rows promoted to fresh or complete (the send is delivered before the snapshot)" },
  { id: "restart-mid-turn", scenario: "restart-busy",
    expected: "`restored`, eligibility unknown, word `unknown` with a last-known note",
    red: "the restore removed (no last-known note), or promoted (eligibility known)" },
  { id: "restored-then-changes", scenario: "restart-changes",
    expected: "still `restored` and unknown until a complete snapshot is accepted", red: "changes treated as completing the restore" },
  { id: "cold-start", scenario: "cold",
    expected: "a spawn request with an empty initial message; the run stays queued; the claim withholds until comms accepts the new lifetime's record, then the run is claimed",
    red: "the send-time hold skips the backing step (no spawn request; the run waits forever)" },
  { id: "run-running-idle", scenario: "running", expected: "the word reads `working`", red: "promotion skipped at the view" },
  { id: "stop-restart", scenario: "stop",
    expected: "`stopped` (a send is stored, not run), then `online`, with queued work drained",
    red: "the view's stop row skipped; restart leaves `unknown`" },
];
