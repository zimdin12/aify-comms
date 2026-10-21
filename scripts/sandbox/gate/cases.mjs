// The frozen cases of plan s4-hard-switch page 2, as scenarios over a Pair (pair.mjs). Each scenario returns its
// `marks`; the judges (judges.mjs) decide. A case's `expected` and `red` are page 2's words, or, where the gate owner
// amended page 2, the words of that amendment in docs/evidence/2026-10-21-sw3a-gate/AMENDMENTS.md (`amendment`).
// Several cases share one scenario where page 2 chains them (silenced, then resumed); each keeps its own judge.

import { randomUUID } from "node:crypto";
import { sleep } from "../env-against-comms.mjs";
import { AGENT, Pair, SECOND_SENDER } from "./pair.mjs";
import { FIRST_PASS_MS, MISSED_REPORTS, announcedAt, applied, candidateWord, ms } from "./judges.mjs";

const WINDOW_MS = 20_000; // how long a withheld run is watched, against a claimer polling every ~2 s
const MARGIN_MS = 5_000; // past the derived silence, so the send lands clearly beyond it
const SECOND_EVERY_MS = 1_000; // the stale second holder's announced interval: the schema's lowest bound
// A watcher snapshot taken while comms restarted carries only an error; every helper reads it as holding nothing.
const run = (seen, id) => seen?.runs?.find((r) => r.id === id) ?? null;
const record = (seen) => seen?.records?.find((r) => r.instance === "default") ?? null;
const publisher = (seen) => seen?.publishers?.find((p) => p.instance === "default") ?? null;
/** The record comms last applied, as the word it read and the time it applied it: what a last-known note must say. */
const priorOf = (seen) => ({ status: candidateWord(record(seen)?.state), appliedAt: publisher(seen)?.data_applied_at ?? null });

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

// Waits on the watcher's timeline, the record the judges read: a direct snapshot can see the claim before the watcher
// writes it, and the evidence read straight after then holds no claim (SW3b q3, legacy-busy).
const claimed = (pair, runId, timeoutMs) => pair.waitFor(() => pair.timeline().some((f) => {
  const r = run(f, runId);
  return r && r.status !== "queued";
}), timeoutMs, `${runId} claimed`).catch(() => null);

async function busy(pair) {
  pair.control({ turn: "busy" });
  return pair.waitRecord((r) => r.busy === true, 60_000, "aify-env's busy record");
}

/** Silence aify-env's publications and wait out three of the intervals it announced, measured from its last contact
 *  (decision 2). With no announcement nothing is derived and nothing is waited: the judge fails the fixture. */
async function silenced(pair) {
  pair.silence(true);
  await sleep(1000);
  const contact = pair.contact();
  const lastContactAt = applied(contact).at(-1)?.t ?? null;
  const announcedMs = lastContactAt === null ? null : announcedAt(contact, lastContactAt);
  if (Number.isInteger(announcedMs)) {
    const due = MISSED_REPORTS * announcedMs + MARGIN_MS;
    await pair.waitFor(() => pair.snapshot().t - lastContactAt >= due, due + 120_000, "the derived silence");
  }
  return { lastContactAt, announcedMs };
}

/** A second publisher key on aify-env's machine holding the agent, announcing SECOND_EVERY_MS, left to go stale. */
async function staleSecondHolder(pair) {
  const machineId = publisher(pair.snapshot()).machine_id;
  const posted = pair.api("POST", "/agent-state", { kind: "snapshot", machineId, instance: "gate-second",
    generation: Date.now(), incarnationId: randomUUID(), publication: 1, inputs: { operatorStop: "tracked" },
    complete: true, reportEveryMs: SECOND_EVERY_MS,
    agents: [{ agentId: AGENT, lifetime: randomUUID(), state: "idle", stateCause: "at-prompt", busy: false,
      process: { state: "running", verified: "yes", pid: 4242 }, turn: null }] });
  const second = pair.snapshot().publishers.find((p) => p.instance === "gate-second");
  const secondAppliedAt = ms(second?.data_applied_at);
  if (secondAppliedAt) {
    const due = MISSED_REPORTS * SECOND_EVERY_MS + MARGIN_MS;
    await pair.waitFor(() => pair.snapshot().t - secondAppliedAt >= due, due + 60_000, "the second holder to go stale");
  }
  return { secondStatus: posted.status, secondAnnouncedMs: SECOND_EVERY_MS, secondAppliedAt };
}

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
    return { runId: s.runId, preLegacyBusy: pre.legacy[0]?.turn_busy ?? 0, preEnvBusy: record(pre)?.busy, holdWindow,
      idleAccepted: accepted(evidence().timeline, (x) => record(x)?.busy === false, idleT) };
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
    const second = await staleSecondHolder(pair);
    const pre = pair.snapshot();
    const s = sent(pair, "C4 stale second holder");
    return { ...second, runId: s.runId, sentAt: s.sentAt, envBusyAtSend: record(pre)?.busy, window: await window(pair) };
  },
  async "busy-beside-unknown"(pair) {
    await pair.warm();
    await busy(pair);
    const second = await staleSecondHolder(pair);
    const pre = pair.snapshot();
    const s = sent(pair, "C14 fresh busy beside unknown, steerable");
    return { ...second, runId: s.runId, sentAt: s.sentAt, envBusyAtSend: record(pre)?.busy, window: await window(pair) };
  },
  async silenced(pair, evidence) {
    await pair.warm();
    await busy(pair);
    const silence = await silenced(pair);
    // Read once the silence holds, so it is the last record comms applied before it: nothing is applied during it.
    const seen = pair.snapshot();
    const prior = priorOf(seen);
    const word = seen.word;
    const q = sent(pair, "C5 queueIfBusy while not reporting", { queueIfBusy: true, steer: false });
    const d = sent(pair, "C7 default while not reporting", { from_agent: SECOND_SENDER });
    const held = await window(pair);
    const resumedAt = pair.snapshot().t;
    pair.silence(false);
    // The phase R2 discriminates (review section 6.1): comms accepts a fresh BUSY record, and the agent stays busy for
    // a whole claim pass, in which the queueIfBusy send must stay queued. Only then is idle requested.
    const freshBusy = (x) => ms(publisher(x)?.data_applied_at) >= resumedAt - 1000 && record(x)?.busy === true;
    await pair.waitFor(() => freshBusy(pair.snapshot()), 180_000, "a fresh busy record after the resume").catch(() => null);
    // The phase is measured on the watcher's frame clock, the clock the fixture check reads, with a second's margin.
    const busyFrom = accepted(evidence().timeline, freshBusy, resumedAt)?.seenAt ?? pair.snapshot().t;
    await pair.waitFor(() => pair.snapshot().t - busyFrom >= FIRST_PASS_MS + 1000, FIRST_PASS_MS + 60_000,
      "a whole claim pass of fresh busy").catch(() => null);
    const idleRequestedAt = pair.snapshot().t;
    pair.control({ turn: "idle" });
    await pair.waitRecord((r) => r.busy === false, 180_000, "aify-env's idle record after the resume").catch(() => null);
    await claimed(pair, q.runId, 30_000);
    await claimed(pair, d.runId, 30_000);
    const timeline = evidence().timeline;
    return { ...silence, prior, word, sentAt: Math.min(q.sentAt, d.sentAt), queueRunId: q.runId, defaultRunId: d.runId,
      window: held, resumedAt, idleRequestedAt, freshBusyAccepted: accepted(timeline, freshBusy, resumedAt),
      freshAccepted: accepted(timeline, (x) => ms(publisher(x)?.data_applied_at) >= resumedAt - 1000, resumedAt),
      idleAccepted: accepted(timeline, (x) => record(x)?.busy === false, resumedAt) };
  },
  async "restart-idle"(pair, evidence) {
    await pair.warm();
    await nextSnapshot(pair);
    const restartedAt = await pair.restartComms();
    const s = sent(pair, "C8 restart while idle");
    const wordBefore = pair.snapshot().word;
    await pair.waitPublication("snapshot", restartedAt, 90_000).catch(() => null);
    await claimed(pair, s.runId, 30_000);
    return { runId: s.runId, sentAt: s.sentAt, restartedAt, wordBefore,
      snapshotAccepted: accepted(evidence().timeline, (x) => publisher(x)?.kind === "snapshot"
        && ms(publisher(x)?.data_applied_at) > restartedAt) };
  },
  async "restart-busy"(pair, evidence) {
    await pair.warm();
    const pre = await busy(pair);
    await nextSnapshot(pair);
    const prior = priorOf(pair.snapshot());
    const restartedAt = await pair.restartComms();
    const read = pair.snapshot();
    await pair.waitPublication("snapshot", restartedAt, 90_000).catch(() => null);
    return { preEnvBusy: record(pre)?.busy, prior, restartedAt, readAt: read.t, word: read.word,
      snapshotAccepted: accepted(evidence().timeline, (x) => publisher(x)?.kind === "snapshot"
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
  /** Page 2's population, constructed: the relay refuses aify-env's publications from before it starts, so comms holds
   *  no record of the agent from any publisher when the send is made (review R3). */
  async cold(pair) {
    await pair.waitFor(() => pair.api("GET", "/environments").json?.environments?.some((e) => e.spawnClaim?.canClaim),
      120_000, "aify-env able to claim spawns");
    const preRecords = pair.snapshot().records;
    const s = sent(pair, "C11 cold start");
    await pair.waitFor(() => pair.standin().some((e) => e.event === "registered"), 180_000, "the stand-in to register");
    const held = await window(pair);
    pair.silence(false);
    await pair.waitRecord((r) => r.lifetime === pair.lifetime(), 180_000, "the new lifetime's record").catch(() => null);
    await claimed(pair, s.runId, 30_000);
    return { runId: s.runId, sentAt: s.sentAt, preRecords, window: held };
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
  async stop(pair, evidence) {
    await pair.warm();
    const before = pair.lifetime();
    // The queued work the restart must drain: a named non-steerable run, held by both readers' busy (review R7).
    legacyTurn(pair);
    await busy(pair);
    const seed = sent(pair, "C13 seeded before the stop", { queueIfBusy: true, steer: false });
    await sleep(5000);
    const seedStatusAtStop = run(pair.snapshot(), seed.runId)?.status ?? null;
    const stopped = pair.api("POST", `/agents/${AGENT}/control`, { action: "stop", from_agent: "dashboard" });
    if (!stopped.ok) throw new Error(`the stop was refused: ${stopped.status} ${stopped.text}`);
    pair.control({ turn: "idle" }); // the next lifetime starts idle rather than inheriting this one's turn
    await pair.waitRecord((r) => r.state === "stopped", 90_000, "aify-env's stopped record");
    await sleep(2000);
    const wordStopped = pair.snapshot().word;
    const spawnsBefore = pair.snapshot().spawns.length;
    const deliveredBefore = pair.standin().filter((e) => e.event === "delivered").length;
    const s = sent(pair, "C13 send while stopped");
    await sleep(WINDOW_MS);
    const spawnsAfterSend = pair.snapshot().spawns.length - spawnsBefore;
    const deliveredWhileStopped = pair.standin().filter((e) => e.event === "delivered").length - deliveredBefore;
    const startT = pair.snapshot().t;
    const started = pair.api("POST", `/agents/${AGENT}/control`, { action: "start", from_agent: "dashboard" });
    if (!started.ok) throw new Error(`the start was refused: ${started.status} ${started.text}`);
    await pair.waitFor(() => { const l = pair.lifetime(); return l && l !== before; }, 120_000, "a new lifetime");
    const after = pair.lifetime();
    // Checkpoint 1, the restart: the first snapshot holding the new lifetime's record, and the word read with it.
    const atRestart = await pair.waitRecord((r) => r.lifetime === after, 180_000, "the new lifetime's record").catch(() => null);
    // Checkpoint 2, its first turn: aify-env publishes a fresh lifetime as turn-unknown until it sees one (A6).
    await busy(pair);
    pair.control({ turn: "idle" });
    await pair.waitRecord((r) => r.busy === false && r.state === "idle", 60_000, "the new lifetime idle");
    await claimed(pair, seed.runId, 30_000);
    await sleep(3000);
    return { seedRunId: seed.runId, seedStatusAtStop, wordStopped, sendStored: s.answer.json?.stored === true,
      sendRunId: s.runId, spawnsAfterSend, deliveredWhileStopped, wordAtRestart: atRestart?.word ?? null,
      restartAccepted: accepted(evidence().timeline, (x) => record(x)?.lifetime === after, startT),
      wordAfterFirstTurn: pair.snapshot().word };
  },
  /** NOT a page 2 case (amendment A3): the whole aify-env daemon stops, claimer and publisher together. Recorded as
   *  what happens, and never judged: comms reads the environment offline at its own 90 s claimer window. */
  async "daemon-lost"(pair) {
    await pair.warm();
    await busy(pair);
    const contact = pair.contact();
    const lastContactAt = applied(contact).at(-1)?.t ?? null;
    const announcedMs = lastContactAt === null ? null : announcedAt(contact, lastContactAt);
    pair.pauseEnv();
    const due = MISSED_REPORTS * (announcedMs ?? 60_000) + MARGIN_MS;
    await pair.waitFor(() => pair.snapshot().t - lastContactAt >= due, due + 120_000, "the silence");
    const word = pair.snapshot().word;
    const q = sent(pair, "daemon lost: queueIfBusy", { queueIfBusy: true, steer: false });
    const d = sent(pair, "daemon lost: default", { from_agent: SECOND_SENDER });
    const environment = pair.api("GET", "/environments").json?.environments?.[0] ?? null;
    pair.resumeEnv();
    return { announcedMs, waitedMs: due, word, queueRunId: q.runId, defaultRunId: d.runId,
      queueAnswer: q.answer.json, defaultAnswer: d.answer.json,
      environment: environment && { status: environment.status, spawnClaim: environment.spawnClaim } };
  },
};

/** The cases, in page 2's order, then the gate owner's additions. `amendment` names the AMENDMENTS.md entry whose
 *  words a case carries in place of page 2's, or that changes how page 2's case is realised. */
export const CASES = [
  { id: "legacy-busy-env-idle", scenario: "legacy-busy", amendment: "A1", expected: "delivered at once",
    red: "the reader still takes the legacy table (it holds)" },
  { id: "legacy-idle-env-busy", scenario: "legacy-idle",
    expected: "held; delivered on the first claim pass after comms accepts the idle publication",
    red: "the reader takes the legacy table (it delivers mid-turn)" },
  { id: "busy-steerable", scenario: "steer", expected: "steered", red: "the carve-out is removed" },
  { id: "stale-second-holder", scenario: "stale-second", amendment: "A4",
    expected: "eligibility unknown: withheld, steer included", red: "stale holders are ignored (known false: delivered)" },
  { id: "env-paused", scenario: "silenced", amendment: "A2",
    expected: "after three announced intervals of publication silence: `unknown` with the prior record's word and application time; sends queue; claims withhold, steer included",
    red: "unknown read as false (a send is delivered during the silence)" },
  { id: "env-resumed", scenario: "silenced", amendment: "A2",
    expected: "queued work drains on the first claim pass after comms accepts a fresh publication", red: "the queue never drains" },
  { id: "default-send-unknown", scenario: "silenced", amendment: "A2", expected: "queued, not claimed",
    red: "the claim gate withholds only `queueIfBusy` runs" },
  { id: "restart-idle", scenario: "restart-idle",
    expected: "before aify-env's next complete snapshot: `restored`, eligibility unknown, a send queues; after it: delivered",
    red: "restored rows promoted to fresh or complete (the send is delivered before the snapshot)" },
  { id: "restart-mid-turn", scenario: "restart-busy", amendment: "A7",
    expected: "`restored`, eligibility unknown, word `unknown` with a last-known note",
    red: "the restore removed (no last-known note), or promoted (eligibility known)" },
  { id: "restored-then-changes", scenario: "restart-changes",
    expected: "still `restored` and unknown until a complete snapshot is accepted", red: "changes treated as completing the restore" },
  { id: "cold-start", scenario: "cold", amendment: "A8",
    expected: "a spawn request with an empty initial message; the run stays queued; the claim withholds until comms accepts the new lifetime's record, then the run is claimed",
    red: "the send-time hold skips the backing step (no spawn request; the run waits forever)" },
  { id: "run-running-idle", scenario: "running", expected: "the word reads `working`", red: "promotion skipped at the view" },
  { id: "stop-restart", scenario: "stop", amendment: "A6",
    expected: "`stopped` (a send is stored, not run), then `online`, with queued work drained",
    red: "the view's stop row skipped; restart leaves `unknown`" },
  { id: "fresh-busy-beside-unknown", scenario: "busy-beside-unknown", amendment: "A5",
    expected: "eligibility unknown beside a fresh busy holder: withheld, steer included",
    red: "steer checked before eligibility (the steerable run is steered)" },
];

/** How a scenario's pair starts: the cold start's relay refuses publications before aify-env exists. */
export const START = { cold: { silenced: true } };

/** Recorded every attempt, never judged: fault histories outside page 2's cases. */
export const OBSERVATIONS = [{ id: "daemon-lost", scenario: "daemon-lost", amendment: "A3" }];
