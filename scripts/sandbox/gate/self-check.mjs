// The gate's judging, proven without Docker: every judge passes its model and fails each single-condition
// countermodel; the healthy models carry the lawful events that follow a hold (review R1). Every case's frozen words
// are page 2's own or an amendment's, read from the documents, never copied here twice. The review's own synthetic
// histories (sw3a-source-1792542290852) are among the models, named "review".

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CASES, OBSERVATIONS, SCENARIOS, START } from "./cases.mjs";
import { JUDGES } from "./judges.mjs";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const PAGE_2 = path.join(REPO, "docs", "superpowers", "plans", "s4-hard-switch", "2-sandbox-gate.md");
const AMENDMENTS = path.join(REPO, "docs", "evidence", "2026-10-21-sw3a-gate", "AMENDMENTS.md");
const T0 = Date.parse("2026-10-21T00:00:00Z");
const iso = (t) => (t == null ? null : new Date(Math.floor(t / 1000) * 1000).toISOString().replace(".000Z", "Z"));
const run = (id, status, claimedT = null, extra = {}) => ({ id, status, claimed_at: iso(claimedT), queue_if_busy: 1,
  steer_if_busy: 0, ...extra });
const steerable = { queue_if_busy: 0, steer_if_busy: 1 };
/** One watcher snapshot. `record: null` is comms holding no record of the agent yet. */
const snap = (t, { runs = [], record = { busy: false, state: "idle", lifetime: "L1" }, applied = t, kind = "changes",
  spawns = [], controls = [], stamps = {} } = {}) => ({
  t, ...stamps, runs, spawns, controls, legacy: [], word: null,
  publishers: [{ instance: "default", kind, data_applied_at: iso(applied) }],
  records: record ? [{ instance: "default", appliedAt: iso(applied), ...record }] : [],
});
const polling = (from, to) => Array.from({ length: Math.floor((to - from) / 2000) + 1 }, (_, i) => ({ event: "polled", t: from + i * 2000 }));
const delivered = (runId, t) => ({ event: "delivered", runId, t });
const window = { from: T0, to: T0 + 20_000 };
const queued = (id, extra) => [snap(T0 + 100, { runs: [run(id, "queued", null, extra)] })];
const report = (t, reportEveryMs = 60_000) => ({ t, instance: "default", kind: "changes", forwarded: true, status: 204, reportEveryMs });
const refused = (t) => ({ t, instance: "default", kind: "changes", forwarded: false, status: 503, reportEveryMs: 60_000 });
const steer = (runId, t) => ({ run_id: runId, action: "steer", requested_at: iso(t) });
const prior = { status: "working", appliedAt: iso(T0 - 190_000) };

/** A stale second holder beside aify-env's holder: the shared shape of A4 and A5. */
function secondHolder(busy) {
  const marks = { runId: "R", sentAt: T0, secondStatus: 204, secondAnnouncedMs: 1000, secondAppliedAt: T0 - 10_000,
    envBusyAtSend: busy, window };
  const pass = { marks, contact: [report(T0 - 20_000)], timeline: queued("R", steerable),
    standin: [...polling(T0, T0 + 20_000), delivered("R", T0 + 26_000)] };
  pass.timeline.push(snap(T0 + 26_200, { runs: [run("R", "completed", T0 + 26_000, steerable)] }));
  return [pass,
    ["the second holder's report refused", { ...pass, marks: { ...marks, secondStatus: 422 } }],
    ["the second holder announced nothing", { ...pass, marks: { ...marks, secondAnnouncedMs: null } }],
    ["the second holder not stale", { ...pass, marks: { ...marks, secondAppliedAt: T0 - 1000 } }],
    ["aify-env announced nothing", { ...pass, contact: [{ ...report(T0 - 20_000), reportEveryMs: null }] }],
    ["aify-env out of contact", { ...pass, contact: [report(T0 - 200_000)] }],
    [busy ? "aify-env not busy" : "aify-env busy", { ...pass, marks: { ...marks, envBusyAtSend: !busy } }],
    ["claimed inside the window", { ...pass, timeline: [snap(T0 + 5200, { runs: [run("R", "completed", T0 + 5000, steerable)] })] }],
    ["received inside the window", { ...pass, standin: [...pass.standin, delivered("R", T0 + 5000)] }],
    ["steered inside the window", { ...pass, timeline: [{ ...pass.timeline[0], controls: [steer("R", T0 + 5000)] }] }],
    ["no claimer polling", { ...pass, standin: [] }],
    // Review F1 for controls: the window ends at 20.900 and comms stamps a steer at 20.500 with the second 20.000. Each
    // sight is [emitted t, acquiredFrom, previousSeenFrom] or { error } (probe.py `watch`); the last one shows the steer.
    ...[["steered in the window's last second, seen before it ends", [[T0 + 20_800]], undefined],
      ["steered in the window's last second, read absent after it ends", [[T0 + 20_950, T0 + 20_920], [T0 + 21_500, T0 + 21_420]], true],
      ["review F1.b: an error read after the end is not an empty inventory", [[T0 + 20_400, T0 + 20_350],
        { t: T0 + 20_950, acquiredFrom: T0 + 20_920, previousSeenFrom: T0 + 20_350, error: "OperationalError: database is locked" },
        [T0 + 21_500, T0 + 21_420, T0 + 20_920]], "UNKNOWN"],
      ["a steer straddling the window's end with no millisecond evidence", [[T0 + 21_500]], "UNKNOWN"]]
      .map(([name, sights, expect]) => [name, { ...pass, marks: { ...marks, window: { from: T0, to: T0 + 20_900 } },
        timeline: [pass.timeline[0], ...sights.map((sight, i) => (Array.isArray(sight)
          ? snap(sight[0], { runs: [run("R", "queued", null, steerable)], stamps: { acquiredFrom: sight[1], previousSeenFrom: sight[2] },
            controls: i === sights.length - 1 ? [steer("R", T0 + 20_500)] : [] }) : sight)),
        snap(T0 + 26_200, { runs: [run("R", "completed", T0 + 26_000, steerable)], controls: [steer("R", T0 + 20_500)] })] }, expect])];
}

/** The silenced scenario's shared marks (A2): busy, then three announced intervals without a report. */
const silenced = { announcedMs: 60_000, lastContactAt: T0 - 190_000, sentAt: T0, window,
  contact: [report(T0 - 190_000), refused(T0 - 100_000), refused(T0 - 10_000)] };

/** case id -> [the passing model, ...[name, countermodel]] */
const MODELS = {
  "legacy-busy-env-idle": (() => {
    const pass = { marks: { runId: "R", sentAt: T0, preLegacyBusy: 1, preEnvBusy: false },
      timeline: [snap(T0 + 1500, { runs: [run("R", "completed", T0 + 1000)] })], standin: [delivered("R", T0 + 1000)] };
    return [pass,
      ["legacy not busy", { ...pass, marks: { ...pass.marks, preLegacyBusy: 0 } }],
      ["env not idle", { ...pass, marks: { ...pass.marks, preEnvBusy: true } }],
      ["held", { ...pass, timeline: queued("R"), standin: [] }],
      ["delivered late", { ...pass, timeline: [snap(T0 + 21_000, { runs: [run("R", "completed", T0 + 20_000)] })] }],
      ["never received as a run", { ...pass, standin: [] }],
      ["became a control", { ...pass, timeline: [{ ...pass.timeline[0], controls: [steer("R", T0 + 500)] }] }]];
  })(),
  "legacy-idle-env-busy": (() => {
    const marks = { runId: "R", preLegacyBusy: 0, preEnvBusy: true, holdWindow: window,
      idleAccepted: { at: T0 + 25_000, seenAt: T0 + 25_200 } };
    const claimedAt = (t, seen = t + 200) => [snap(T0 + 100, { runs: [run("R", "queued")] }),
      snap(seen, { runs: [run("R", "completed", t)] })];
    // Healthy: held through the window, then the lawful release, received by the stand-in after it (R1).
    const pass = { marks, timeline: claimedAt(T0 + 27_000), standin: [...polling(T0, T0 + 20_000), delivered("R", T0 + 27_000)] };
    const lawful = { marks: { ...marks, idleAccepted: { at: T0 + 21_000, seenAt: T0 + 21_000 } }, timeline: claimedAt(T0 + 22_000, T0 + 22_000),
      standin: [...polling(T0, T0 + 20_000), delivered("R", T0 + 22_000)] };
    // Review F1: the hold ends at 20.900, so a claim comms stamps with the second 20.000 straddles its end. `sights` are
    // the watcher frames that see it queued, the last one claimed, each [emitted t, acquiredFrom, previousSeenFrom]
    // (probe.py `watch`); `idle` is the idle acceptance [at, seenAt].
    const edge = (sights, { claim = T0 + 20_970, idle = [T0 + 20_000, T0 + 20_950], receipt = null } = {}) => ({
      marks: { ...marks, holdWindow: { from: T0, to: T0 + 20_900 }, idleAccepted: { at: idle[0], seenAt: idle[1] } },
      timeline: [snap(T0 + 100, { runs: [run("R", "queued")] }), ...sights.map(([t, acquiredFrom, previousSeenFrom], i) =>
        snap(t, { runs: [i === sights.length - 1 ? run("R", "completed", claim) : run("R", "queued")], stamps: { acquiredFrom, previousSeenFrom } }))],
      standin: [...polling(T0, T0 + 20_000), ...(receipt ? [delivered("R", receipt)] : [])] });
    return [pass,
      ["review R1: a hold, idle at 21 s, then a claim and receipt at 22 s passes", { lawful }],
      ["review F1: released in the hold's last second, read queued after it ends",
        edge([[T0 + 20_950, T0 + 20_920], [T0 + 21_500, T0 + 21_420, T0 + 20_920]], { receipt: T0 + 20_975 }), true],
      ["review F1.a: a queued frame emitted after the end but read before it",
        edge([[T0 + 20_950, T0 + 20_880], [T0 + 21_500, T0 + 21_420, T0 + 20_880]], { receipt: T0 + 20_975 }), "UNKNOWN"],
      ["a read still queued after the end, recorded on the next frame",
        edge([[T0 + 20_950, T0 + 20_880], [T0 + 21_500, T0 + 21_420, T0 + 20_930]], { receipt: T0 + 20_975 }), true],
      ["review F1: released in the next second", edge([[T0 + 21_100], [T0 + 21_650]], { claim: T0 + 21_150,
        idle: [T0 + 21_000, T0 + 21_100], receipt: T0 + 21_160 }), true],
      ["claimed in the hold's last second, seen claimed before it ends", edge([[T0 + 20_800]], { idle: [T0 + 20_000, T0 + 20_500] })],
      ["received in the hold's last second, before it ends", edge([[T0 + 20_950, T0 + 20_920], [T0 + 21_500, T0 + 21_420]], { receipt: T0 + 20_890 })],
      ["claimed in an earlier second, seen late, with no receipt", edge([[T0 + 21_500]], { claim: T0 + 19_500, idle: [T0 + 19_000, T0 + 21_400] })],
      ["a claim straddling the hold's end with no millisecond evidence", edge([[T0 + 21_500]]), "UNKNOWN"],
      ["legacy busy", { ...pass, marks: { ...marks, preLegacyBusy: 1 } }],
      ["env not busy", { ...pass, marks: { ...marks, preEnvBusy: false } }],
      ["claimed inside the hold", { ...pass, timeline: claimedAt(T0 + 5000) }],
      ["received inside the hold", { ...pass, standin: [...pass.standin, delivered("R", T0 + 10_000)] }],
      ["claimed before the acceptance", { ...pass, timeline: claimedAt(T0 + 23_000) }],
      // comms' seconds say before, while the watcher happened to see the claim later: only the seconds catch it.
      ["claimed a second before the acceptance, seen after it", { ...pass, timeline: claimedAt(T0 + 24_000, T0 + 25_300) }],
      ["seen claimed first in the same second", { ...pass, timeline: claimedAt(T0 + 25_000, T0 + 25_100),
        marks: { ...marks, idleAccepted: { at: T0 + 25_000, seenAt: T0 + 25_600 } } }],
      ["claimed after the first pass", { ...pass, timeline: claimedAt(T0 + 40_000) }],
      ["never claimed", { ...pass, timeline: queued("R") }],
      ["no claimer polling", { ...pass, standin: [] }],
      ["acceptance never seen", { ...pass, marks: { ...marks, idleAccepted: null } }]];
  })(),
  "busy-steerable": (() => {
    const pass = { marks: { runId: "R", sentAt: T0, preEnvBusy: true, idleSeenBeforeReceipt: false }, timeline: [],
      standin: [delivered("R", T0 + 2000)] };
    return [pass,
      ["env not busy", { ...pass, marks: { ...pass.marks, preEnvBusy: false } }],
      ["never received", { ...pass, standin: [] }],
      ["received late", { ...pass, standin: [delivered("R", T0 + 30_000)] }],
      ["idle before receipt", { ...pass, marks: { ...pass.marks, idleSeenBeforeReceipt: true } }]];
  })(),
  "stale-second-holder": secondHolder(false),
  "fresh-busy-beside-unknown": (() => {
    const [pass, ...counters] = secondHolder(true);
    return [pass, ...counters, ["the send not steerable", { ...pass, timeline: [snap(T0 + 100, { runs: [run("R", "queued")] })] }]];
  })(),
  "env-paused": (() => {
    const marks = { ...silenced, prior, word: { status: "unknown", lastKnown: { status: "working", appliedAt: prior.appliedAt } },
      queueRunId: "Q", defaultRunId: "D" };
    delete marks.contact;
    const runs = (q, d) => [snap(T0 + 100, { runs: [q, d] })];
    const pass = { marks, contact: silenced.contact, timeline: runs(run("Q", "queued"), run("D", "queued", null, steerable)),
      standin: polling(T0, T0 + 20_000) };
    return [pass,
      ["a release after the window is lawful", { ...pass, timeline: [...pass.timeline, snap(T0 + 30_000, { runs: [
        run("Q", "completed", T0 + 29_000), run("D", "completed", T0 + 25_000, steerable)] })],
      standin: [...pass.standin, delivered("D", T0 + 25_000), delivered("Q", T0 + 29_000)] }, true],
      ["aify-env announced nothing", { ...pass, marks: { ...marks, announcedMs: null } }],
      ["three intervals not passed", { ...pass, marks: { ...marks, lastContactAt: T0 - 100_000 } }],
      ["a report reached comms during the silence", { ...pass, contact: [...silenced.contact, report(T0 - 50_000)] }],
      ["aify-env not seen trying", { ...pass, contact: [report(T0 - 190_000)] }],
      ["the word not unknown", { ...pass, marks: { ...marks, word: { ...marks.word, status: "online" } } }],
      ["no lastKnown", { ...pass, marks: { ...marks, word: { status: "unknown" } } }],
      ["review R8: negative prose in statusNote", { ...pass, marks: { ...marks,
        word: { status: "unknown", statusNote: "No last known record or application timestamp exists" } } }],
      ["lastKnown names another word", { ...pass, marks: { ...marks, word: { status: "unknown", lastKnown: { ...prior, status: "online" } } } }],
      ["lastKnown names another time", { ...pass, marks: { ...marks, word: { status: "unknown", lastKnown: { ...prior, appliedAt: iso(T0) } } } }],
      ["the queueIfBusy send claimed in the window", { ...pass, timeline: runs(run("Q", "completed", T0 + 5000), run("D", "queued", null, steerable)) }],
      ["the steerable send steered in the window", { ...pass, timeline: [{ ...pass.timeline[0], controls: [steer("D", T0 + 5000)] }] }],
      ["the steerable send received in the window", { ...pass, standin: [...pass.standin, delivered("D", T0 + 4000)] }],
      ["a send created no run", { ...pass, marks: { ...marks, queueRunId: null } }]];
  })(),
  "env-resumed": (() => {
    const marks = { queueRunId: "Q", defaultRunId: "D", resumedAt: T0, freshAccepted: { at: T0 + 3000, seenAt: T0 + 3200 },
      idleAccepted: { at: T0 + 10_000, seenAt: T0 + 10_200 } };
    const tl = (q, d, qSeen = q + 200, dSeen = d + 200) => [snap(T0 - 100, { runs: [run("Q", "queued"), run("D", "queued", null, steerable)] }),
      ...[[q, qSeen, "Q"], [d, dSeen, "D"]].filter(([t]) => t !== null).sort((a, b) => a[1] - b[1]).map(([, seen], i, all) =>
        snap(seen, { runs: [all.slice(0, i + 1).some(([, , id]) => id === "Q") ? run("Q", "completed", q) : run("Q", "queued"),
          all.slice(0, i + 1).some(([, , id]) => id === "D") ? run("D", "completed", d, steerable) : run("D", "queued", null, steerable)] }))];
    // Healthy: the steerable send drains as soon as a fresh busy publication is accepted, the queueIfBusy one after idle.
    const pass = { marks, timeline: tl(T0 + 11_000, T0 + 4000), standin: [] };
    return [pass,
      ["no fresh publication after the resume", { ...pass, marks: { ...marks, freshAccepted: { at: T0 - 5000, seenAt: T0 - 4800 } } }],
      ["review R2: the queueIfBusy send claimed while busy, before idle", { ...pass, timeline: tl(T0 + 6000, T0 + 4000) }],
      ["the queueIfBusy send seen claimed before idle in the same second", { ...pass, timeline: tl(T0 + 10_000, T0 + 4000, T0 + 10_100) }],
      ["the queueIfBusy send late", { ...pass, timeline: tl(T0 + 25_000, T0 + 4000) }],
      ["the queueIfBusy send never drained", { ...pass, timeline: tl(null, T0 + 4000) }],
      ["the steerable send before the fresh publication", { ...pass, timeline: tl(T0 + 11_000, T0 + 2000) }],
      ["the steerable send a second before the fresh publication, seen after it", { ...pass, timeline: tl(T0 + 11_000, T0 + 2000, T0 + 11_200, T0 + 3300) }],
      ["the steerable send late", { ...pass, timeline: tl(T0 + 11_000, T0 + 25_000) }],
      ["the steerable send never drained", { ...pass, timeline: tl(T0 + 11_000, null) }],
      ["no queueIfBusy run", { ...pass, marks: { ...marks, queueRunId: null } }],
      ["no steerable run", { ...pass, marks: { ...marks, defaultRunId: null } }],
      ["idle acceptance never seen", { ...pass, marks: { ...marks, idleAccepted: null } }]];
  })(),
  "default-send-unknown": (() => {
    const marks = { ...silenced, defaultRunId: "D" };
    delete marks.contact;
    const pass = { marks, contact: silenced.contact, timeline: queued("D", steerable), standin: polling(T0, T0 + 20_000) };
    return [pass,
      ["not a default send", { ...pass, timeline: queued("D", { queue_if_busy: 1 }) }],
      ["the send created no run", { ...pass, marks: { ...marks, defaultRunId: null }, timeline: [] }],
      ["claimed", { ...pass, timeline: [snap(T0 + 3000, { runs: [run("D", "completed", T0 + 2000, steerable)] })] }],
      ["aify-env announced nothing", { ...pass, marks: { ...marks, announcedMs: null } }],
      ["no claimer polling", { ...pass, standin: [] }]];
  })(),
  "restart-idle": (() => {
    const marks = { runId: "R", sentAt: T0, restartedAt: T0 - 5000, wordBefore: { status: "unknown" },
      snapshotAccepted: { at: T0 + 30_000, seenAt: T0 + 30_300 } };
    const claimed = (t) => [snap(T0 + 100, { runs: [run("R", "queued")] }), snap(t + 200, { runs: [run("R", "completed", t)] })];
    const pass = { marks, timeline: claimed(T0 + 33_000), standin: [] };
    return [pass,
      ["snapshot before the send", { ...pass, marks: { ...marks, snapshotAccepted: { at: T0 - 1000, seenAt: T0 - 800 } } }],
      ["word known before the snapshot", { ...pass, marks: { ...marks, wordBefore: { status: "online" } } }],
      ["delivered before the snapshot", { ...pass, timeline: claimed(T0 + 5000) }],
      ["delivered late", { ...pass, timeline: claimed(T0 + 45_000) }],
      ["never delivered", { ...pass, timeline: queued("R") }]];
  })(),
  "restart-mid-turn": (() => {
    const marks = { preEnvBusy: true, readAt: T0, prior, word: { status: "unknown", lastKnown: { ...prior } },
      snapshotAccepted: { at: T0 + 30_000 } };
    const pass = { marks, timeline: [], standin: [] };
    return [pass,
      ["not mid-turn", { ...pass, marks: { ...marks, preEnvBusy: false } }],
      ["read after the snapshot", { ...pass, marks: { ...marks, readAt: T0 + 40_000 } }],
      ["word known", { ...pass, marks: { ...marks, word: { status: "working", lastKnown: { ...prior } } } }],
      ["no lastKnown", { ...pass, marks: { ...marks, word: { status: "unknown" } } }],
      ["review R8: negative prose in statusNote", { ...pass, marks: { ...marks,
        word: { status: "unknown", statusNote: "No last known record or application timestamp exists" } } }],
      ["a bare truthy lastKnown", { ...pass, marks: { ...marks, word: { status: "unknown", lastKnown: true } } }],
      ["lastKnown names another word", { ...pass, marks: { ...marks, word: { status: "unknown", lastKnown: { ...prior, status: "online" } } } }],
      ["no prior to compare", { ...pass, marks: { ...marks, prior: null } }]];
  })(),
  "restored-then-changes": (() => {
    const marks = { restartedAt: T0, changesAccepted: { at: T0 + 5000 }, snapshotAccepted: { at: T0 + 40_000 },
      wordAtChanges: { status: "unknown" }, wordAfterSnapshot: { status: "online" } };
    const pass = { marks, timeline: [], standin: [] };
    return [pass,
      ["no changes after the restart", { ...pass, marks: { ...marks, changesAccepted: null } }],
      ["snapshot before the changes", { ...pass, marks: { ...marks, snapshotAccepted: { at: T0 + 3000 } } }],
      ["changes completed the restore", { ...pass, marks: { ...marks, wordAtChanges: { status: "online" } } }],
      ["unknown after the snapshot", { ...pass, marks: { ...marks, wordAfterSnapshot: { status: "unknown" } } }]];
  })(),
  "cold-start": (() => {
    const spawn = [{ id: "S", initial_message: "" }];
    const hold = { from: T0 + 5000, to: T0 + 25_000 };
    const withRecord = { record: { busy: false, state: "unknown", lifetime: "L2" }, applied: T0 + 30_000 };
    const tl = (claimT, spawns = spawn) => [snap(T0, { record: null, spawns, runs: [run("R", "queued", null, steerable)] }),
      ...(claimT < T0 + 30_000 ? [snap(claimT + 200, { record: null, spawns, runs: [run("R", "completed", claimT, steerable)] })] : []),
      snap(T0 + 30_000, { ...withRecord, spawns, runs: [claimT < T0 + 30_000 ? run("R", "completed", claimT, steerable) : run("R", "queued", null, steerable)] }),
      ...(claimT >= T0 + 30_000 && claimT < Infinity ? [snap(claimT + 200, { ...withRecord, spawns, runs: [run("R", "completed", claimT, steerable)] })] : [])];
    const pass = { marks: { runId: "R", preRecords: [], window: hold }, lifetime: "L2", timeline: tl(T0 + 32_000),
      standin: [...polling(hold.from, hold.to), delivered("R", T0 + 32_000)] };
    return [pass,
      ["review R3: an available row with no lifetime before the send", { ...pass, marks: { ...pass.marks,
        preRecords: [{ instance: "default", lifetime: null, busy: false, state: "available" }] } }],
      ["the population not read back", { ...pass, marks: { ...pass.marks, preRecords: undefined } }],
      ["no spawn request", { ...pass, timeline: tl(T0 + 32_000, []) }],
      ["an initial message", { ...pass, timeline: tl(T0 + 32_000, [{ id: "S", initial_message: "hello" }]) }],
      ["the stand-in never started", { ...pass, lifetime: null }],
      ["claimed inside the hold", { ...pass, timeline: tl(T0 + 10_000) }],
      ["claimed after the hold but before the record", { ...pass, timeline: tl(T0 + 28_000) }],
      ["claimed late", { ...pass, timeline: tl(T0 + 50_000) }],
      ["never claimed", { ...pass, timeline: tl(Infinity) }],
      ["no claimer polling in the hold", { ...pass, standin: [delivered("R", T0 + 32_000)] }]];
  })(),
  "run-running-idle": (() => {
    const marks = { runStatus: "running", record: { busy: false, state: "idle" }, word: { status: "working" } };
    const pass = { marks, timeline: [], standin: [] };
    return [pass,
      ["run not running", { ...pass, marks: { ...marks, runStatus: "completed" } }],
      ["record busy", { ...pass, marks: { ...marks, record: { busy: true, state: "working" } } }],
      ["promotion skipped", { ...pass, marks: { ...marks, word: { status: "online" } } }]];
  })(),
  "stop-restart": (() => {
    const marks = { seedRunId: "S", seedStatusAtStop: "queued", wordStopped: { status: "stopped" }, sendStored: true,
      spawnsAfterSend: 0, deliveredWhileStopped: 0, restartAccepted: { at: T0 + 60_000, seenAt: T0 + 60_200 },
      wordAtRestart: { status: "unknown" }, wordAfterFirstTurn: { status: "online" } };
    const seedAt = (t) => [snap(T0 + 100, { runs: [run("S", "queued")] }), ...(t === null ? [] : [snap(t + 200, { runs: [run("S", "completed", t)] })])];
    // Healthy (review R7's matched control): the named pre-stop run, delivered after the restart.
    const pass = { marks, timeline: seedAt(T0 + 62_000), standin: [delivered("S", T0 + 62_000)] };
    return [pass,
      ["review R7: no queued work seeded", { ...pass, marks: { ...marks, seedRunId: null } }],
      ["the seed not queued at the stop", { ...pass, marks: { ...marks, seedStatusAtStop: "completed" } }],
      ["review R7: the named seed never drained", { ...pass, timeline: seedAt(null) }],
      ["the seed claimed before the restart", { ...pass, timeline: seedAt(T0 + 30_000) }],
      ["stop row skipped", { ...pass, marks: { ...marks, wordStopped: { status: "online" } } }],
      ["send not stored", { ...pass, marks: { ...marks, sendStored: false } }],
      ["the send started it", { ...pass, marks: { ...marks, spawnsAfterSend: 1 } }],
      ["delivered while stopped", { ...pass, marks: { ...marks, deliveredWhileStopped: 1 } }],
      ["the restart never accepted", { ...pass, marks: { ...marks, restartAccepted: null } }],
      ["still stopped at the restart", { ...pass, marks: { ...marks, wordAtRestart: { status: "stopped" } } }],
      ["restart leaves unknown after the first turn", { ...pass, marks: { ...marks, wordAfterFirstTurn: { status: "unknown" } } }]];
  })(),
};

export function selfCheck() {
  const lines = [];
  const check = (name, ok) => lines.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  const page = fs.readFileSync(PAGE_2, "utf8").replace(/\r\n/g, "\n");
  const flat = (text) => text.replace(/\s+/g, " ");
  // Each amendment's own section, its wrapped lines joined: a case's words must be in the entry it names.
  const sections = Object.fromEntries(fs.readFileSync(AMENDMENTS, "utf8").replace(/\r\n/g, "\n").split(/^## /m).slice(1)
    .map((s) => [s.slice(0, s.indexOf(".")), flat(s)]));
  const rows = (page.match(/^\| [^|]+\| [^|]+\| [^|]+\|$/gm) ?? []).length - 1; // less the header row
  const isNew = (c) => Boolean(c.amendment && sections[c.amendment]?.includes("**Page 2:** none"));
  check(`page 2's ${rows} rows are thirteen, each a case, and every other case is an amendment's new one`,
    rows === 13 && CASES.filter((c) => !isNew(c)).length === rows);
  for (const c of [...CASES, ...OBSERVATIONS]) {
    if (c.amendment) check(`${c.id}: amendment ${c.amendment} is recorded`, Boolean(sections[c.amendment]));
  }
  for (const c of CASES) {
    const source = (words) => page.includes(words) || Boolean(c.amendment && sections[c.amendment]?.includes(flat(words)));
    check(`${c.id}: its expected answer is page 2's or its amendment's words`, source(c.expected));
    check(`${c.id}: its RED is page 2's or its amendment's words`, source(c.red));
    check(`${c.id}: has a scenario and a judge`, typeof SCENARIOS[c.scenario] === "function" && typeof JUDGES[c.id] === "function");
    const [model, ...counters] = MODELS[c.id] ?? [];
    if (!model) { check(`${c.id}: has models`, false); continue; }
    const why = (v) => ` [${v.verdict}: ${[...v.failed, ...v.unknown].join("; ")}]`;
    const passed = JUDGES[c.id](model);
    check(`${c.id}: control, the model passes${passed.verdict === "PASS" ? "" : why(passed)}`, passed.verdict === "PASS");
    for (const [name, counter, expect] of counters) {
      // A named pair { lawful } is a second healthy model, as is a third element `true`; "UNKNOWN" expects that verdict.
      const evidence = counter.lawful ?? counter;
      const want = counter.lawful || expect === true ? "PASS" : expect === "UNKNOWN" ? "UNKNOWN" : "FAIL";
      const got = JUDGES[c.id](evidence);
      // `pass` is what the gate counts, so it must agree with the verdict: an UNKNOWN is never a pass.
      const held = got.verdict === want && got.pass === (want === "PASS");
      check(`${c.id}: ${name} ${{ PASS: "passes", FAIL: "fails", UNKNOWN: "reads UNKNOWN" }[want]}${held ? "" : why(got)}`, held);
    }
  }
  check("every judge is used by a case", Object.keys(JUDGES).every((id) => CASES.some((c) => c.id === id)));
  check("every observation has a scenario", OBSERVATIONS.every((o) => typeof SCENARIOS[o.scenario] === "function"));
  check("every start option names a scenario", Object.keys(START).every((s) => typeof SCENARIOS[s] === "function"));
  for (const line of lines) console.log(line);
  const failed = lines.filter((l) => l.startsWith("FAIL")).length;
  console.log(`${lines.length - failed}/${lines.length} self-check witnesses passed`);
  return failed ? 1 : 0;
}
