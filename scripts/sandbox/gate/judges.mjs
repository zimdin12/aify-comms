// The gate's judges: PURE, over what a scenario gathered (`marks`, comms' change-only `timeline`, the stand-in's
// `standin` log). Each returns {pass, failed: [the conditions that did not hold]}, so a RED names its reason.
//
// Clocks: comms' rows carry whole seconds; the timeline's `t` and the stand-in's `t` are milliseconds on the same Docker
// VM clock. An ordering is judged on comms' seconds (">=" admits a tie within the second) AND on the order the watcher
// first saw the two events, so a tie inside one second cannot pass a claim that the watcher saw first.

export const FIRST_PASS_MS = 10_000; // "the first claim pass": the stand-in long-polls 2 s; comms' fallback is 3 s
export const RECEIPT_SILENCE_MS = 180_000; // decision 2: receipt freshness on comms' own clock
export const MIN_POLLS = 3; // a withheld run is judged against a claimer seen asking at least this often

export const ms = (iso) => (iso ? Date.parse(String(iso).endsWith("Z") ? iso : `${iso}Z`) : null);
const runOf = (snapshot, runId) => snapshot?.runs?.find((r) => r.id === runId) ?? null;
const last = (timeline) => timeline.at(-1) ?? {};

/** The run's claim on comms' clock, and the watcher time it was first seen claimed; nulls while it never was. */
export function claimOf(timeline, runId) {
  const seen = timeline.find((s) => { const r = runOf(s, runId); return r && r.status !== "queued"; });
  const run = runOf(seen, runId);
  return { claimedAt: ms(run?.claimed_at), seenAt: seen?.t ?? null, status: runOf(last(timeline), runId)?.status ?? null };
}

/** The first snapshot at or after `fromT` (watcher time) matching `test`, or null. */
export const firstSeen = (timeline, test, fromT = 0) => timeline.find((s) => s.t >= fromT && test(s)) ?? null;
const envRecord = (s) => s?.records?.find((r) => r.instance === "default") ?? null;
export const polls = (standin, from, to) => standin.filter((e) => e.event === "polled" && e.t >= from && e.t <= to).length;
const received = (standin, runId) => standin.find((e) => (e.event === "delivered" || e.event === "control") && e.runId === runId);
const controlsFor = (timeline, runId) => (last(timeline).controls ?? []).filter((c) => c.run_id === runId);
const lastKnownShown = (word) => Boolean(word?.lastKnown) || /last[ -]?known/i.test(String(word?.statusNote ?? ""));

/** Claimed no earlier than `acceptedAt` (comms seconds and watcher order both) and within the first pass after it. */
function claimedOnFirstPassAfter(timeline, runId, accepted, failed, label) {
  const claim = claimOf(timeline, runId);
  if (claim.claimedAt === null) return failed.push(`${label}: ${runId} was never claimed`);
  if (accepted?.at == null) return failed.push(`${label}: the acceptance it waits for was never seen`);
  if (claim.claimedAt < accepted.at) failed.push(`${label}: claimed before comms accepted the publication`);
  if (accepted.seenAt != null && claim.seenAt < accepted.seenAt) failed.push(`${label}: the watcher saw the claim first`);
  if (claim.claimedAt - accepted.at > FIRST_PASS_MS) failed.push(`${label}: not claimed on the first pass after acceptance`);
}

/** Never claimed through the window, against a claimer that kept asking. */
function withheld(ev, runId, window, failed, label) {
  const claim = claimOf(ev.timeline, runId);
  if (!runId) return failed.push(`${label}: the send created no run`);
  if (claim.claimedAt !== null && claim.claimedAt <= window.to) failed.push(`${label}: ${runId} was claimed inside the window`);
  if (received(ev.standin, runId)) failed.push(`${label}: the stand-in received ${runId}`);
  if (polls(ev.standin, window.from, window.to) < MIN_POLLS) failed.push(`${label}: the stand-in was not seen polling`);
}

const verdict = (failed) => ({ pass: failed.length === 0, failed });

export const JUDGES = {
  "legacy-busy-env-idle"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.preLegacyBusy !== 1) failed.push("fixture: comms' legacy turn was not busy");
    if (m.preEnvBusy !== false) failed.push("fixture: aify-env's fresh record was not idle");
    const claim = claimOf(ev.timeline, m.runId);
    if (claim.claimedAt === null) failed.push("held: never claimed");
    else if (claim.claimedAt - m.sentAt > FIRST_PASS_MS) failed.push("not delivered at once");
    if (!ev.standin.some((e) => e.event === "delivered" && e.runId === m.runId)) failed.push("the stand-in never received it as a run");
    if (controlsFor(ev.timeline, m.runId).length) failed.push("it became a control, not a delivery");
    return verdict(failed);
  },
  "legacy-idle-env-busy"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.preLegacyBusy === 1) failed.push("fixture: comms' legacy turn was busy");
    if (m.preEnvBusy !== true) failed.push("fixture: aify-env's fresh record was not busy");
    withheld(ev, m.runId, m.holdWindow, failed, "held");
    claimedOnFirstPassAfter(ev.timeline, m.runId, m.idleAccepted, failed, "delivered after the idle publication");
    return verdict(failed);
  },
  "busy-steerable"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.preEnvBusy !== true) failed.push("fixture: aify-env's fresh record was not busy");
    const got = received(ev.standin, m.runId);
    if (!got) failed.push("not steered: the stand-in never received it");
    else if (got.t - m.sentAt > FIRST_PASS_MS) failed.push("not steered at once");
    if (m.idleSeenBeforeReceipt) failed.push("fixture: the agent went idle before it arrived");
    return verdict(failed);
  },
  "stale-second-holder"(ev) {
    const { marks: m } = ev, failed = [];
    if (!(m.sentAt - m.secondAppliedAt > RECEIPT_SILENCE_MS)) failed.push("fixture: the second holder was not stale");
    if (!(m.sentAt - m.envAppliedAt < RECEIPT_SILENCE_MS)) failed.push("fixture: aify-env's holder was not fresh");
    if (m.envBusyAtSend !== false) failed.push("fixture: aify-env's holder was not idle");
    withheld(ev, m.runId, m.window, failed, "withheld");
    if (controlsFor(ev.timeline, m.runId).length) failed.push("steered while eligibility was unknown");
    return verdict(failed);
  },
  "env-paused"(ev) {
    const { marks: m } = ev, failed = [];
    if (!(m.sentAt - m.envAppliedAt > RECEIPT_SILENCE_MS)) failed.push("fixture: 180 s of receipt silence had not passed");
    if (m.word?.status !== "unknown") failed.push(`the word read ${m.word?.status}, not unknown`);
    if (!lastKnownShown(m.word)) failed.push("no stale last-known note beside unknown");
    withheld(ev, m.queueRunId, m.window, failed, "the queueIfBusy send");
    for (const runId of [m.queueRunId, m.defaultRunId]) {
      if (controlsFor(ev.timeline, runId).length) failed.push(`${runId} was steered while unknown`);
    }
    return verdict(failed);
  },
  "env-resumed"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.freshAccepted?.at == null) failed.push("no fresh publication was accepted after the resume");
    for (const runId of [m.queueRunId, m.defaultRunId]) {
      if (!runId) { failed.push("no queued work to drain: a send created no run"); continue; }
      const claim = claimOf(ev.timeline, runId);
      if (claim.claimedAt === null) { failed.push(`${runId} never drained`); continue; }
      if (claim.claimedAt < m.freshAccepted?.at) failed.push(`${runId} drained before a fresh publication was accepted`);
      if (m.idleAccepted?.at == null || claim.claimedAt - m.idleAccepted.at > FIRST_PASS_MS) {
        failed.push(`${runId} did not drain on the first pass after the idle publication`);
      }
    }
    return verdict(failed);
  },
  "default-send-unknown"(ev) {
    const { marks: m } = ev, failed = [];
    if (!(m.sentAt - m.envAppliedAt > RECEIPT_SILENCE_MS)) failed.push("fixture: eligibility was not unknown");
    const run = runOf(ev.timeline.find((s) => runOf(s, m.defaultRunId)), m.defaultRunId);
    if (run && run.queue_if_busy !== 0) failed.push("fixture: the send was not a default send");
    withheld(ev, m.defaultRunId, m.window, failed, "the default send");
    return verdict(failed);
  },
  "restart-idle"(ev) {
    const { marks: m } = ev, failed = [];
    if (!(m.snapshotAccepted?.at > m.sentAt)) failed.push("fixture: the send was not made before the next snapshot");
    if (m.wordBefore?.status !== "unknown") failed.push(`before the snapshot the word read ${m.wordBefore?.status}`);
    claimedOnFirstPassAfter(ev.timeline, m.runId, m.snapshotAccepted, failed, "after the snapshot");
    return verdict(failed);
  },
  "restart-mid-turn"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.preEnvBusy !== true) failed.push("fixture: the agent was not mid-turn");
    if (!(m.snapshotAccepted?.at > m.readAt)) failed.push("fixture: the read was not before the next snapshot");
    if (m.word?.status !== "unknown") failed.push(`the word read ${m.word?.status}, not unknown`);
    if (!lastKnownShown(m.word)) failed.push("no last-known note beside unknown");
    return verdict(failed);
  },
  "restored-then-changes"(ev) {
    const { marks: m } = ev, failed = [];
    if (!(m.changesAccepted?.at >= m.restartedAt)) failed.push("fixture: no changes publication after the restart");
    if (!(m.snapshotAccepted?.at > m.changesAccepted?.at)) failed.push("fixture: the snapshot came before the changes");
    if (m.wordAtChanges?.status !== "unknown") failed.push(`after the changes the word read ${m.wordAtChanges?.status}`);
    if (m.wordAfterSnapshot?.status === "unknown") failed.push("still unknown after the complete snapshot");
    return verdict(failed);
  },
  "cold-start"(ev) {
    const { marks: m } = ev, failed = [];
    const spawns = last(ev.timeline).spawns ?? [];
    if (!spawns.length) failed.push("no spawn request");
    else if (spawns.some((s) => s.initial_message !== "")) failed.push("a spawn request carried an initial message");
    if (!ev.lifetime) failed.push("the stand-in never started");
    const seen = firstSeen(ev.timeline, (s) => envRecord(s)?.lifetime === ev.lifetime);
    const accepted = seen ? { at: ms(envRecord(seen).appliedAt), seenAt: seen.t } : null;
    claimedOnFirstPassAfter(ev.timeline, m.runId, accepted, failed, "claimed after the new lifetime's record");
    return verdict(failed);
  },
  "run-running-idle"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.runStatus !== "running") failed.push(`fixture: the run read ${m.runStatus}, not running`);
    if (m.record?.busy !== false || m.record?.state !== "idle") failed.push("fixture: the record was not idle");
    if (m.word?.status !== "working") failed.push(`the word read ${m.word?.status}, not working`);
    return verdict(failed);
  },
  "stop-restart"(ev) {
    const { marks: m } = ev, failed = [];
    if (m.wordStopped?.status !== "stopped") failed.push(`after the stop the word read ${m.wordStopped?.status}`);
    if (m.sendStored !== true) failed.push("the send to the stopped agent was not stored");
    if (m.spawnsAfterSend > 0) failed.push("the send to the stopped agent started it");
    if (m.deliveredWhileStopped > 0) failed.push("something was delivered while stopped");
    if (m.wordAfter?.status !== "online") failed.push(`after the restart the word read ${m.wordAfter?.status}`);
    for (const runId of m.queuedAtStart ?? []) {
      const claim = claimOf(ev.timeline, runId);
      if (claim.claimedAt === null) failed.push(`${runId} queued at the restart never drained`);
    }
    return verdict(failed);
  },
};

