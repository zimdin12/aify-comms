// The gate's judges: PURE, over what a scenario gathered: `marks`, comms' change-only `timeline` (DATA, runs, words),
// the stand-in's `standin` log, and the relay's `contact` log (every report aify-env sent and what became of it). Each
// returns {pass, verdict, failed, unknown}: a RED names its reason, and an UNKNOWN names what could not be resolved.
//
// Clocks: comms' rows carry whole seconds; the timeline's, the stand-in's and the relay's `t` are milliseconds on the
// one Docker VM clock. An ordering is judged on comms' seconds (">=" admits a tie within the second) AND on the order the
// watcher first saw the two events, so a tie inside one second cannot pass a claim that the watcher saw first. A
// whole-second stamp against a millisecond window end is a one-second bucket, placed by `byEnd` (review F1).
//
// Contact, DATA and eligibility stay apart (review R5). Contact is the relay's: a report comms answered 204 (applied).
// DATA is comms' rows. Eligibility is never read from either: it is what the claim gate DID (claimed or withheld) and
// the word comms showed. The silence a case waits for is derived from the interval aify-env announced, three of them
// (DECISIONS: "aify-env announces its reporting interval"); an absent announcement derives nothing and fails the fixture.

export const FIRST_PASS_MS = 10_000; // "the first claim pass": the stand-in long-polls 2 s; comms' fallback is 3 s
export const MISSED_REPORTS = 3; // decision 2: not reporting once three announced intervals pass without a receipt
export const MIN_POLLS = 3; // a withheld run is judged against a claimer seen asking at least this often

export const ms = (iso) => (iso ? Date.parse(String(iso).endsWith("Z") ? iso : `${iso}Z`) : null);
const runOf = (snapshot, runId) => snapshot?.runs?.find((r) => r.id === runId) ?? null;
const last = (timeline) => timeline.at(-1) ?? {};
const envRecord = (s) => s?.records?.find((r) => r.instance === "default") ?? null;

/** The latest time a SQL read is known to have found `absent(frame)` before frame `index`, or null. Only lower bounds
 *  count (review F1.a): a frame's content was read at or after its `acquiredFrom`, and still held at or after the next
 *  frame's `previousSeenFrom` (probe.py `watch`). The emitted `t` never counts. A frame with an error, or without the
 *  `inventory` it is asked about, says nothing (F1.b); a frame without acquisition stamps gives no bound. */
export function absentThrough(timeline, index, inventory, absent) {
  let through = null;
  for (let j = 0; j < index; j += 1) {
    const frame = timeline[j];
    if (frame.error || !Array.isArray(frame[inventory]) || !absent(frame)) continue;
    const bound = timeline[j + 1]?.previousSeenFrom ?? frame.acquiredFrom;
    if (Number.isFinite(bound) && (through === null || bound > through)) through = bound;
  }
  return through;
}

/** The run's claim on comms' clock, the watcher time it was first seen claimed, and the latest acquisition known to
 *  have read it still queued before that; nulls where there is no such sight. */
export function claimOf(timeline, runId) {
  const index = timeline.findIndex((s) => { const r = runOf(s, runId); return r && r.status !== "queued"; });
  const seen = timeline[index] ?? null;
  return { claimedAt: ms(runOf(seen, runId)?.claimed_at), seenAt: seen?.t ?? null,
    queuedThrough: absentThrough(timeline, index < 0 ? timeline.length : index, "runs", (s) => runOf(s, runId)?.status === "queued"),
    status: runOf(last(timeline), runId)?.status ?? null };
}

/** The first snapshot at or after `fromT` (watcher time) matching `test`, or null. */
export const firstSeen = (timeline, test, fromT = 0) => timeline.find((s) => s.t >= fromT && test(s)) ?? null;
export const polls = (standin, from, to) => standin.filter((e) => e.event === "polled" && e.t >= from && e.t <= to).length;
/** A receipt of the run (delivered, or a control on it) at or before `to`: only the forbidden interval counts (R1). */
const receivedBy = (standin, runId, to) => standin.find((e) => (e.event === "delivered" || e.event === "control")
  && e.runId === runId && e.t <= to);
/** Every control on the run that a successful frame showed, once per identity (review F1.b). A failed read never erases
 *  a control already seen, and a control only a failed read mentions is never seen. */
export function controlsOn(timeline, runId) {
  const known = new Map();
  for (const frame of timeline) {
    if (frame.error || !Array.isArray(frame.controls)) continue;
    for (const c of frame.controls.filter((x) => x.run_id === runId)) {
      const identity = c.id ?? `${c.action}@${c.requested_at}`;
      if (!known.has(identity)) known.set(identity, c);
    }
  }
  return [...known.values()];
}
const earliest = (...times) => times.filter((t) => t != null).reduce((a, b) => Math.min(a, b), Infinity);

export const SECOND_MS = 1000;
/** Whether an event comms stamped with the whole second `stampMs` happened by the millisecond `to` (review F1). The
 *  stamp places the event only in [stamp, stamp + 1 s). Where that bucket straddles `to`, a millisecond observation
 *  decides it: seen by `to` (the watcher, or the stand-in's receipt) is inside, still seen absent after `to` is outside.
 *  With neither, the answer is "unknown", never a breach. */
export function byEnd(stampMs, to, { seenBy = Infinity, absentAfter = null } = {}) {
  if (stampMs === null || stampMs > to) return "outside";
  if (stampMs + SECOND_MS - 1 <= to) return "inside";
  if (seenBy <= to) return "inside";
  if (absentAfter !== null && absentAfter > to) return "outside";
  return "unknown";
}

/** Each control on the run, placed against `to` from its requested_at second, the watcher's first sight of it or the
 *  stand-in's receipt of it, and the latest successful controls inventory known to have been read without it. */
function controlsByEnd(ev, runId, to) {
  const received = ev.standin.find((e) => e.event === "control" && e.runId === runId)?.t;
  return controlsOn(ev.timeline, runId).map((c) => {
    const same = (x) => x.run_id === runId && x.action === c.action && x.requested_at === c.requested_at;
    const index = ev.timeline.findIndex((s) => (s.controls ?? []).some(same));
    return byEnd(ms(c.requested_at), to, { seenBy: earliest(ev.timeline[index]?.t, received),
      absentAfter: absentThrough(ev.timeline, index, "controls", (s) => !(s.controls ?? []).some(same)) });
  });
}

/** The word comms' candidate shows for a record state (`candidate_word`): idle reads online. */
export const candidateWord = (state) => (state === "idle" ? "online" : state ?? null);

/** The applied reports from aify-env's own key, in order, from the relay's log. */
export const applied = (contact) => (contact ?? []).filter((e) => e.instance === "default" && e.forwarded && e.status === 204);
/** The interval aify-env announced in its last applied report at or before `at`; null when it announced none. */
export function announcedAt(contact, at) {
  const report = applied(contact).filter((e) => e.t <= at).at(-1);
  return Number.isInteger(report?.reportEveryMs) ? report.reportEveryMs : null;
}

/** Publication silence held over [from, to]: no applied report from the env key, and the env was seen trying. */
function silenceHeld(contact, from, to, failed) {
  if (applied(contact).some((e) => e.t > from && e.t <= to)) failed.push("fixture: a report reached comms during the silence");
  if (!(contact ?? []).some((e) => e.instance === "default" && !e.forwarded && e.t > from && e.t <= to)) {
    failed.push("fixture: aify-env was not seen trying to report during the silence");
  }
}

/** The derived silence passed before the send: three announced intervals since the last contact (R5). */
function silenceDerived(m, failed) {
  if (!Number.isInteger(m.announcedMs)) return failed.push("fixture: aify-env announced no interval, so no silence is derived");
  if (!(m.sentAt - m.lastContactAt >= MISSED_REPORTS * m.announcedMs)) failed.push("fixture: three announced intervals had not passed");
}

/** The last-known note carries the prior record's word and its application time, as values (R8). */
function lastKnownMatches(word, prior, failed) {
  const note = word?.lastKnown;
  if (!prior?.status || !prior?.appliedAt) return failed.push("fixture: no prior applied record to compare against");
  if (!note || typeof note !== "object") return failed.push("no lastKnown beside unknown");
  if (note.status !== prior.status) failed.push(`lastKnown.status ${note.status}, not the prior ${prior.status}`);
  if (ms(note.appliedAt) !== ms(prior.appliedAt)) failed.push(`lastKnown.appliedAt ${note.appliedAt}, not ${prior.appliedAt}`);
}

/** Claimed no earlier than `accepted` (comms seconds and watcher order both) and within the first pass after it. */
function claimedOnFirstPassAfter(timeline, runId, accepted, failed, label) {
  const claim = claimOf(timeline, runId);
  if (claim.claimedAt === null) return failed.push(`${label}: ${runId} was never claimed`);
  if (accepted?.at == null) return failed.push(`${label}: the acceptance it waits for was never seen`);
  if (claim.claimedAt < accepted.at) failed.push(`${label}: claimed before comms accepted the publication`);
  if (accepted.seenAt != null && claim.seenAt < accepted.seenAt) failed.push(`${label}: the watcher saw the claim first`);
  if (claim.claimedAt - accepted.at > FIRST_PASS_MS) failed.push(`${label}: not claimed on the first pass after acceptance`);
}

/** Neither claimed nor received nor steered inside the window, against a claimer that kept asking. After `window.to`
 *  a release is lawful, so nothing later counts here (R1). A whole-second stamp is placed by `byEnd` (F1). */
function withheld(ev, runId, window, failed, unknown, label) {
  if (!runId) return failed.push(`${label}: the send created no run`);
  const claim = claimOf(ev.timeline, runId);
  const delivered = ev.standin.find((e) => e.event === "delivered" && e.runId === runId)?.t;
  const claimed = byEnd(claim.claimedAt, window.to, { seenBy: earliest(claim.seenAt, delivered), absentAfter: claim.queuedThrough });
  if (claimed === "inside") failed.push(`${label}: ${runId} was claimed inside the window`);
  if (claimed === "unknown") unknown.push(`${label}: ${runId}'s claim second straddles the window's end, and nothing finer orders them`);
  if (receivedBy(ev.standin, runId, window.to)) failed.push(`${label}: the stand-in received ${runId} inside the window`);
  const steered = controlsByEnd(ev, runId, window.to);
  if (steered.includes("inside")) failed.push(`${label}: ${runId} was steered inside the window`);
  else if (steered.includes("unknown")) unknown.push(`${label}: a control's second on ${runId} straddles the window's end, and nothing finer orders them`);
  if (polls(ev.standin, window.from, window.to) < MIN_POLLS) failed.push(`${label}: the stand-in was not seen polling`);
}

/** A stale second holder beside aify-env's holder, judged by announcements on both sides (R5, R6). */
function secondHolderFixture(ev, m, busy, failed) {
  if (m.secondStatus !== 204) failed.push(`fixture: the second holder's report was refused (${m.secondStatus})`);
  if (!Number.isInteger(m.secondAnnouncedMs)) failed.push("fixture: the second holder announced no interval");
  else if (!(m.sentAt - m.secondAppliedAt >= MISSED_REPORTS * m.secondAnnouncedMs)) failed.push("fixture: the second holder was not stale");
  const envMs = announcedAt(ev.contact, m.sentAt);
  const lastEnv = applied(ev.contact).filter((e) => e.t <= m.sentAt).at(-1);
  if (!Number.isInteger(envMs)) failed.push("fixture: aify-env announced no interval");
  else if (!lastEnv || m.sentAt - lastEnv.t >= MISSED_REPORTS * envMs) failed.push("fixture: aify-env's holder was not in contact");
  if (m.envBusyAtSend !== busy) failed.push(`fixture: aify-env's holder was ${busy ? "not busy" : "not idle"}`);
}

/** PASS, FAIL, or UNKNOWN where nothing failed but the instrument could not resolve a condition (F1): never a pass. */
const verdict = (failed, unknown = []) => ({ pass: failed.length === 0 && unknown.length === 0,
  verdict: failed.length ? "FAIL" : unknown.length ? "UNKNOWN" : "PASS", failed, unknown });

export const JUDGES = {
  "legacy-busy-env-idle"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (m.preLegacyBusy !== 1) failed.push("fixture: comms' legacy turn was not busy");
    if (m.preEnvBusy !== false) failed.push("fixture: aify-env's fresh record was not idle");
    const claim = claimOf(ev.timeline, m.runId);
    if (claim.claimedAt === null) failed.push("held: never claimed");
    else if (claim.claimedAt - m.sentAt > FIRST_PASS_MS) failed.push("not delivered at once");
    if (!ev.standin.some((e) => e.event === "delivered" && e.runId === m.runId)) failed.push("the stand-in never received it as a run");
    if (controlsOn(ev.timeline, m.runId).length) failed.push("it became a control, not a delivery");
    return verdict(failed, unknown);
  },
  "legacy-idle-env-busy"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (m.preLegacyBusy === 1) failed.push("fixture: comms' legacy turn was busy");
    if (m.preEnvBusy !== true) failed.push("fixture: aify-env's fresh record was not busy");
    withheld(ev, m.runId, m.holdWindow, failed, unknown, "held");
    claimedOnFirstPassAfter(ev.timeline, m.runId, m.idleAccepted, failed, "delivered after the idle publication");
    return verdict(failed, unknown);
  },
  "busy-steerable"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (m.preEnvBusy !== true) failed.push("fixture: aify-env's fresh record was not busy");
    const got = receivedBy(ev.standin, m.runId, Infinity);
    if (!got) failed.push("not steered: the stand-in never received it");
    else if (got.t - m.sentAt > FIRST_PASS_MS) failed.push("not steered at once");
    if (m.idleSeenBeforeReceipt) failed.push("fixture: the agent went idle before it arrived");
    return verdict(failed, unknown);
  },
  "stale-second-holder"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    secondHolderFixture(ev, m, false, failed);
    withheld(ev, m.runId, m.window, failed, unknown, "withheld");
    return verdict(failed, unknown);
  },
  "fresh-busy-beside-unknown"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    secondHolderFixture(ev, m, true, failed);
    const run = runOf(ev.timeline.find((s) => runOf(s, m.runId)), m.runId);
    if (run && run.steer_if_busy !== 1) failed.push("fixture: the send was not steerable");
    withheld(ev, m.runId, m.window, failed, unknown, "withheld, steer included");
    return verdict(failed, unknown);
  },
  "env-paused"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    silenceDerived(m, failed);
    silenceHeld(ev.contact, m.lastContactAt, m.window.to, failed);
    if (m.word?.status !== "unknown") failed.push(`the word read ${m.word?.status}, not unknown`);
    lastKnownMatches(m.word, m.prior, failed);
    withheld(ev, m.queueRunId, m.window, failed, unknown, "the queueIfBusy send");
    withheld(ev, m.defaultRunId, m.window, failed, unknown, "the steerable send");
    return verdict(failed, unknown);
  },
  "env-resumed"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    // comms' stamp is the whole second, so an acceptance in the resume's own second reads up to 1 s before it.
    if (!(m.freshAccepted?.at >= m.resumedAt - 1000)) failed.push("no fresh publication was accepted after the resume");
    if (!m.queueRunId) failed.push("no queued work to drain: the queueIfBusy send created no run");
    // Non-steer work waits for known idle, in comms' seconds and the watcher's order (R2).
    else claimedOnFirstPassAfter(ev.timeline, m.queueRunId, m.idleAccepted, failed, "the queueIfBusy send");
    // A steerable send may proceed as soon as eligibility is known, busy or not; it is not held to idle (R2).
    if (!m.defaultRunId) failed.push("no queued work to drain: the steerable send created no run");
    else {
      const claim = claimOf(ev.timeline, m.defaultRunId);
      if (claim.claimedAt === null) failed.push("the steerable send never drained");
      else {
        if (claim.claimedAt < m.freshAccepted?.at) failed.push("the steerable send drained before a fresh publication");
        if (m.freshAccepted?.seenAt != null && claim.seenAt < m.freshAccepted.seenAt) failed.push("the watcher saw the steerable claim first");
        if (m.idleAccepted?.at == null || claim.claimedAt - m.idleAccepted.at > FIRST_PASS_MS) {
          failed.push("the steerable send did not drain by the first pass after the idle publication");
        }
      }
    }
    return verdict(failed, unknown);
  },
  "default-send-unknown"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    silenceDerived(m, failed);
    silenceHeld(ev.contact, m.lastContactAt, m.window.to, failed);
    const run = runOf(ev.timeline.find((s) => runOf(s, m.defaultRunId)), m.defaultRunId);
    if (m.defaultRunId && m.defaultRunId === m.queueRunId) failed.push("fixture: the default send joined the queueIfBusy run");
    if (run && run.queue_if_busy !== 0) failed.push("fixture: the send was not a default send");
    withheld(ev, m.defaultRunId, m.window, failed, unknown, "the default send");
    return verdict(failed, unknown);
  },
  "restart-idle"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (!(m.snapshotAccepted?.at > m.sentAt)) failed.push("fixture: the send was not made before the next snapshot");
    if (m.wordBefore?.status !== "unknown") failed.push(`before the snapshot the word read ${m.wordBefore?.status}`);
    claimedOnFirstPassAfter(ev.timeline, m.runId, m.snapshotAccepted, failed, "after the snapshot");
    return verdict(failed, unknown);
  },
  "restart-mid-turn"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (m.preEnvBusy !== true) failed.push("fixture: the agent was not mid-turn");
    if (!(m.snapshotAccepted?.at > m.readAt)) failed.push("fixture: the read was not before the next snapshot");
    if (m.word?.status !== "unknown") failed.push(`the word read ${m.word?.status}, not unknown`);
    lastKnownMatches(m.word, m.prior, failed);
    return verdict(failed, unknown);
  },
  "restored-then-changes"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (!(m.changesAccepted?.at >= m.restartedAt)) failed.push("fixture: no changes publication after the restart");
    if (!(m.snapshotAccepted?.at > m.changesAccepted?.at)) failed.push("fixture: the snapshot came before the changes");
    if (m.wordAtChanges?.status !== "unknown") failed.push(`after the changes the word read ${m.wordAtChanges?.status}`);
    if (m.wordAfterSnapshot?.status === "unknown") failed.push("still unknown after the complete snapshot");
    return verdict(failed, unknown);
  },
  "cold-start"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    // Page 2's frozen input: no record of the agent from any publisher, fresh or stale, read back before the send (R3).
    if (!Array.isArray(m.preRecords)) failed.push("fixture: the no-record population was not read back");
    else if (m.preRecords.length) failed.push(`fixture: comms held ${m.preRecords.length} record(s) of the agent before the send`);
    const spawns = last(ev.timeline).spawns ?? [];
    if (!spawns.length) failed.push("no spawn request");
    else if (spawns.some((s) => s.initial_message !== "")) failed.push("a spawn request carried an initial message");
    if (!ev.lifetime) failed.push("the stand-in never started");
    withheld(ev, m.runId, m.window, failed, unknown, "withheld before the record");
    const seen = firstSeen(ev.timeline, (s) => envRecord(s)?.lifetime === ev.lifetime);
    const accepted = seen ? { at: ms(envRecord(seen).appliedAt), seenAt: seen.t } : null;
    claimedOnFirstPassAfter(ev.timeline, m.runId, accepted, failed, "claimed after the new lifetime's record");
    return verdict(failed, unknown);
  },
  "run-running-idle"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    if (m.runStatus !== "running") failed.push(`fixture: the run read ${m.runStatus}, not running`);
    if (m.record?.busy !== false || m.record?.state !== "idle") failed.push("fixture: the record was not idle");
    if (m.word?.status !== "working") failed.push(`the word read ${m.word?.status}, not working`);
    return verdict(failed, unknown);
  },
  "stop-restart"(ev) {
    const { marks: m } = ev, failed = [], unknown = [];
    // A named run, seeded and queued before the stop (R7). The stop fails it: never claimed or delivered after the
    // restart, and its message left unread in the inbox (amendment A9).
    if (!m.seedRunId) failed.push("fixture: no queued work was seeded before the stop");
    else if (m.seedStatusAtStop !== "queued") failed.push(`fixture: the seeded run was ${m.seedStatusAtStop} at the stop`);
    if (m.wordStopped?.status !== "stopped") failed.push(`after the stop the word read ${m.wordStopped?.status}`);
    if (m.sendStored !== true) failed.push("the send to the stopped agent was not stored");
    if (m.spawnsAfterSend > 0) failed.push("the send to the stopped agent started it");
    if (m.deliveredWhileStopped > 0) failed.push("something was delivered while stopped");
    // Two named checkpoints: the restart (the new lifetime's first accepted record) and its first turn (amendment A6).
    if (!m.restartAccepted?.at) failed.push("fixture: the new lifetime's record was never accepted");
    if (!m.wordAtRestart || m.wordAtRestart.status === "stopped") failed.push(`at the restart the word read ${m.wordAtRestart?.status}`);
    if (m.wordAfterFirstTurn?.status !== "online") failed.push(`after the first turn the word read ${m.wordAfterFirstTurn?.status}`);
    if (m.seedRunId) {
      if (claimOf(ev.timeline, m.seedRunId).claimedAt !== null) failed.push("the seeded run was claimed after the stop");
      if (ev.standin.some((e) => e.event === "delivered" && e.runId === m.seedRunId)) failed.push("the stand-in received the seeded run");
      const end = runOf(ev.timeline.findLast((s) => runOf(s, m.seedRunId)), m.seedRunId)?.status;
      if (end !== "failed") failed.push(`the seeded run ended ${end}, not failed`);
      if (m.seedInInbox === null || m.seedInInbox === undefined) failed.push("fixture: the agent's inbox was not read");
      else if (m.seedInInbox !== true) failed.push("the seeded message is not unread in the agent's inbox");
    }
    return verdict(failed, unknown);
  },
};
