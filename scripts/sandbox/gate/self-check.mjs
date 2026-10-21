// The gate's judging, proven without Docker: every judge passes its model and fails each single-condition
// countermodel, and every case's frozen words are page 2's own (read from the plan, never copied here twice).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CASES, SCENARIOS } from "./cases.mjs";
import { JUDGES } from "./judges.mjs";

const PAGE_2 = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..",
  "docs", "superpowers", "plans", "s4-hard-switch", "2-sandbox-gate.md");
const T0 = Date.parse("2026-10-21T00:00:00Z");
const iso = (t) => (t == null ? null : new Date(Math.floor(t / 1000) * 1000).toISOString().replace(".000Z", "Z"));
const run = (id, status, claimedT = null, extra = {}) => ({ id, status, claimed_at: iso(claimedT), queue_if_busy: 1,
  steer_if_busy: 0, ...extra });
/** One watcher snapshot. `record: null` is comms holding no record of the agent yet. */
const snap = (t, { runs = [], record = { busy: false, state: "idle", lifetime: "L1" }, applied = t, kind = "changes",
  spawns = [], controls = [] } = {}) => ({
  t, runs, spawns, controls, legacy: [], word: null,
  publishers: [{ instance: "default", kind, data_applied_at: iso(applied) }],
  records: record ? [{ instance: "default", appliedAt: iso(applied), ...record }] : [],
});
const polling = (from, to) => Array.from({ length: Math.floor((to - from) / 2000) + 1 }, (_, i) => ({ event: "polled", t: from + i * 2000 }));
const window = { from: T0, to: T0 + 20_000 };
const queued = (id, extra) => [snap(T0 + 100, { runs: [run(id, "queued", null, extra)] })];

/** case id -> [the passing model, ...[name, countermodel]] */
const MODELS = {
  "legacy-busy-env-idle": (() => {
    const pass = { marks: { runId: "R", sentAt: T0, preLegacyBusy: 1, preEnvBusy: false },
      timeline: [snap(T0 + 1500, { runs: [run("R", "completed", T0 + 1000)] })],
      standin: [{ event: "delivered", runId: "R", t: T0 + 1000 }] };
    return [pass,
      ["legacy not busy", { ...pass, marks: { ...pass.marks, preLegacyBusy: 0 } }],
      ["env not idle", { ...pass, marks: { ...pass.marks, preEnvBusy: true } }],
      ["held", { ...pass, timeline: queued("R"), standin: [] }],
      ["delivered late", { ...pass, timeline: [snap(T0 + 21_000, { runs: [run("R", "completed", T0 + 20_000)] })] }],
      ["never received as a run", { ...pass, standin: [] }],
      ["became a control", { ...pass, timeline: [{ ...pass.timeline[0], controls: [{ run_id: "R", action: "steer" }] }] }]];
  })(),
  "legacy-idle-env-busy": (() => {
    const marks = { runId: "R", preLegacyBusy: 0, preEnvBusy: true, holdWindow: window,
      idleAccepted: { at: T0 + 25_000, seenAt: T0 + 25_200 } };
    const claimedAt = (t, seen = t + 200) => [snap(T0 + 100, { runs: [run("R", "queued")] }),
      snap(seen, { runs: [run("R", "completed", t)] })];
    const pass = { marks, timeline: claimedAt(T0 + 27_000), standin: polling(T0, T0 + 20_000) };
    return [pass,
      ["legacy busy", { ...pass, marks: { ...marks, preLegacyBusy: 1 } }],
      ["env not busy", { ...pass, marks: { ...marks, preEnvBusy: false } }],
      ["claimed inside the hold", { ...pass, timeline: claimedAt(T0 + 5000) }],
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
      standin: [{ event: "delivered", runId: "R", t: T0 + 2000 }] };
    return [pass,
      ["env not busy", { ...pass, marks: { ...pass.marks, preEnvBusy: false } }],
      ["never received", { ...pass, standin: [] }],
      ["received late", { ...pass, standin: [{ event: "delivered", runId: "R", t: T0 + 30_000 }] }],
      ["idle before receipt", { ...pass, marks: { ...pass.marks, idleSeenBeforeReceipt: true } }]];
  })(),
  "stale-second-holder": (() => {
    const marks = { runId: "R", sentAt: T0, secondAppliedAt: T0 - 190_000, envAppliedAt: T0 - 30_000, envBusyAtSend: false, window };
    const pass = { marks, timeline: queued("R", { steer_if_busy: 1, queue_if_busy: 0 }), standin: polling(T0, T0 + 20_000) };
    return [pass,
      ["second holder fresh", { ...pass, marks: { ...marks, secondAppliedAt: T0 - 100_000 } }],
      ["env holder stale", { ...pass, marks: { ...marks, envAppliedAt: T0 - 200_000 } }],
      ["env holder busy", { ...pass, marks: { ...marks, envBusyAtSend: true } }],
      ["claimed", { ...pass, timeline: [snap(T0 + 3000, { runs: [run("R", "completed", T0 + 2000)] })] }],
      ["received", { ...pass, standin: [...pass.standin, { event: "delivered", runId: "R", t: T0 + 2000 }] }],
      ["steered", { ...pass, timeline: [{ ...pass.timeline[0], controls: [{ run_id: "R", action: "steer" }] }] }],
      ["no claimer polling", { ...pass, standin: [] }]];
  })(),
  "env-paused": (() => {
    const marks = { word: { status: "unknown", lastKnown: { word: "working" } }, envAppliedAt: T0 - 190_000, sentAt: T0,
      queueRunId: "Q", defaultRunId: "D", window };
    const pass = { marks, timeline: [snap(T0 + 100, { runs: [run("Q", "queued"), run("D", "queued")] })],
      standin: polling(T0, T0 + 20_000) };
    return [pass,
      ["silence not passed", { ...pass, marks: { ...marks, envAppliedAt: T0 - 100_000 } }],
      ["word not unknown", { ...pass, marks: { ...marks, word: { status: "online" } } }],
      ["no last-known note", { ...pass, marks: { ...marks, word: { status: "unknown", lastKnown: null } } }],
      ["queueIfBusy claimed", { ...pass, timeline: [snap(T0 + 3000, { runs: [run("Q", "completed", T0 + 2000), run("D", "queued")] })] }],
      ["steered", { ...pass, timeline: [{ ...pass.timeline[0], controls: [{ run_id: "D", action: "steer" }] }] }],
      ["no claimer polling", { ...pass, standin: [] }]];
  })(),
  "env-resumed": (() => {
    const marks = { queueRunId: "Q", defaultRunId: "D", freshAccepted: { at: T0 + 10_000 }, idleAccepted: { at: T0 + 15_000 } };
    const claims = (q, d) => [snap(T0 + 100, { runs: [run("Q", "queued"), run("D", "queued")] }),
      snap(T0 + 50_000, { runs: [q === null ? run("Q", "queued") : run("Q", "completed", q), run("D", "completed", d)] })];
    const pass = { marks, timeline: claims(T0 + 16_000, T0 + 12_000), standin: [] };
    return [pass,
      ["no fresh publication", { ...pass, marks: { ...marks, freshAccepted: null } }],
      ["drained before the fresh publication", { ...pass, timeline: claims(T0 + 5000, T0 + 12_000) }],
      ["never drained", { ...pass, timeline: claims(null, T0 + 12_000) }],
      ["drained late", { ...pass, timeline: claims(T0 + 40_000, T0 + 12_000) }],
      ["idle acceptance never seen", { ...pass, marks: { ...marks, idleAccepted: null } }],
      ["a send created no run", { ...pass, marks: { ...marks, queueRunId: null } }]];
  })(),
  "default-send-unknown": (() => {
    const marks = { defaultRunId: "D", sentAt: T0, envAppliedAt: T0 - 190_000, window };
    const pass = { marks, timeline: queued("D", { queue_if_busy: 0, steer_if_busy: 1 }), standin: polling(T0, T0 + 20_000) };
    return [pass,
      ["not a default send", { ...pass, timeline: queued("D", { queue_if_busy: 1 }) }],
      ["the send created no run", { ...pass, marks: { ...marks, defaultRunId: null }, timeline: [] }],
      ["claimed", { ...pass, timeline: [snap(T0 + 3000, { runs: [run("D", "completed", T0 + 2000, { queue_if_busy: 0 })] })] }],
      ["eligibility known", { ...pass, marks: { ...marks, envAppliedAt: T0 - 10_000 } }],
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
    const marks = { preEnvBusy: true, readAt: T0, word: { status: "unknown", statusNote: "last known: working" },
      snapshotAccepted: { at: T0 + 30_000 } };
    const pass = { marks, timeline: [], standin: [] };
    return [pass,
      ["not mid-turn", { ...pass, marks: { ...marks, preEnvBusy: false } }],
      ["read after the snapshot", { ...pass, marks: { ...marks, readAt: T0 + 40_000 } }],
      ["word known", { ...pass, marks: { ...marks, word: { status: "working" } } }],
      ["no last-known note", { ...pass, marks: { ...marks, word: { status: "unknown", statusNote: "" } } }]];
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
    const tl = (claimT, spawns = spawn) => [snap(T0, { record: null, spawns, runs: [run("R", "queued")] }),
      ...(claimT < T0 + 20_000 ? [snap(claimT + 200, { record: null, spawns, runs: [run("R", "completed", claimT)] })] : []),
      snap(T0 + 20_000, { record: { busy: false, state: "unknown", lifetime: "L2" }, spawns,
        runs: [claimT < T0 + 20_000 ? run("R", "completed", claimT) : run("R", "queued")] }),
      ...(claimT >= T0 + 20_000 && claimT < Infinity ? [snap(claimT + 200, { record: { busy: false, state: "unknown", lifetime: "L2" },
        applied: T0 + 20_000, spawns, runs: [run("R", "completed", claimT)] })] : [])];
    const pass = { marks: { runId: "R" }, lifetime: "L2", timeline: tl(T0 + 22_000), standin: [] };
    return [pass,
      ["no spawn request", { ...pass, timeline: tl(T0 + 22_000, []) }],
      ["an initial message", { ...pass, timeline: tl(T0 + 22_000, [{ id: "S", initial_message: "hello" }]) }],
      ["the stand-in never started", { ...pass, lifetime: null }],
      ["claimed before the record", { ...pass, timeline: tl(T0 + 5000) }],
      ["claimed late", { ...pass, timeline: tl(T0 + 40_000) }],
      ["never claimed", { ...pass, timeline: tl(Infinity) }]];
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
    const marks = { wordStopped: { status: "stopped" }, sendStored: true, spawnsAfterSend: 0, deliveredWhileStopped: 0,
      wordAfter: { status: "online" }, queuedAtStart: ["R"] };
    const pass = { marks, timeline: [snap(T0 + 60_000, { runs: [run("R", "completed", T0 + 59_000)] })], standin: [] };
    return [pass,
      ["stop row skipped", { ...pass, marks: { ...marks, wordStopped: { status: "online" } } }],
      ["send not stored", { ...pass, marks: { ...marks, sendStored: false } }],
      ["the send started it", { ...pass, marks: { ...marks, spawnsAfterSend: 1 } }],
      ["delivered while stopped", { ...pass, marks: { ...marks, deliveredWhileStopped: 1 } }],
      ["restart leaves unknown", { ...pass, marks: { ...marks, wordAfter: { status: "unknown" } } }],
      ["queued work never drained", { ...pass, timeline: queued("R") }]];
  })(),
};

export function selfCheck() {
  const lines = [];
  const check = (name, ok) => lines.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  const page = fs.readFileSync(PAGE_2, "utf8").replace(/\r\n/g, "\n");
  check("thirteen cases, as page 2's table has thirteen rows", CASES.length === 13
    && (page.match(/^\| [^|]+\| [^|]+\| [^|]+\|$/gm) ?? []).length - 1 === 13); // less the header row
  for (const c of CASES) {
    check(`${c.id}: its expected answer is page 2's words`, page.includes(c.expected));
    check(`${c.id}: its RED is page 2's words`, page.includes(c.red));
    check(`${c.id}: has a scenario and a judge`, typeof SCENARIOS[c.scenario] === "function" && typeof JUDGES[c.id] === "function");
    const [model, ...counters] = MODELS[c.id] ?? [];
    if (!model) { check(`${c.id}: has models`, false); continue; }
    const passed = JUDGES[c.id](model);
    check(`${c.id}: control, the model passes${passed.pass ? "" : ` [${passed.failed.join("; ")}]`}`, passed.pass);
    for (const [name, counter] of counters) check(`${c.id}: ${name} fails`, !JUDGES[c.id](counter).pass);
  }
  check("every judge is used by a case", Object.keys(JUDGES).every((id) => CASES.some((c) => c.id === id)));
  for (const line of lines) console.log(line);
  const failed = lines.filter((l) => l.startsWith("FAIL")).length;
  console.log(`${lines.length - failed}/${lines.length} self-check witnesses passed`);
  return failed ? 1 : 0;
}
