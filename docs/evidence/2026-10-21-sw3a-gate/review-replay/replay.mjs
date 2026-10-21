// The reviews' own synthetic histories, replayed through the judges each review read and through this commit's:
//   round 1, sw3a-source-1792542290852: JUDGE-COHERENT-OBSERVATIONS.json (observations.json), judged by 88efbf12;
//   round 2, sw3a-successor-1792548943247: PARENT-JUDGE-OBSERVATIONS.json (observations-2.json), judged by 5f4effad;
//   round 3, sw3a-f1-1792551632225: PARENT-JUDGE-OBSERVATIONS.json (observations-3.json), judged by ef2264d9;
//   round 4, sw3a-acquisition-1792553532164: TAIL-ERROR-OBSERVATIONS.json (observations-4.json), judged by b7824b6a.
//
//   node docs/evidence/2026-10-21-sw3a-gate/review-replay/replay.mjs > replay.txt
//
// Each row's evidence is used as the review wrote it. Where the current judge reads a mark a round-1 row lacks, the row
// is ADAPTED by adding marks only (nothing removed or changed, so the old judge reads the same input), and the
// adaptation is printed with its reason. The replay checks:
//   - the review's judge on the verbatim evidence gives the review's recorded verdict (the instrument reproduces it);
//   - the review's judge gives the same verdict on the adapted evidence (the adaptation hides nothing from it);
//   - this commit's judge gives the review's oracle: PASS, FAIL, or UNKNOWN where the review asks for it.
// Exit 0 only when every check holds.

import crypto from "node:crypto";
import cp from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Each copy is committed with LF line ends and one final newline, as the repo's whitespace gate requires. The original's
// own line end and file ending are put back before hashing, so the pin still proves the copy is the original byte for byte.
const PINNED = {
  "observations.json": { sha: "7a4c1c5681073ed7f63412dc3226d014b30fab65aba07df1b0622d06d23df418", eol: "\n", ending: "" },
  "judges-88efbf12.mjs": { sha: "86ea74ddf7558eb24189063a4f343265993cfde61f39cbc95eb5a2510252cfea", eol: "\n", ending: "\n\n" },
  "observations-2.json": { sha: "298530f6884078846c992c3376dfa07d868819188fd40311cd2c463f9262cf71", eol: "\r\n", ending: "" },
  "judges-5f4effad.mjs": { sha: "619f645472e1ad779410c123ca45803bf60700726e85ce3ced90875f49e84ac5", eol: "\n", ending: "\n" },
  "observations-3.json": { sha: "c34cac4457f02e6205e268a8ce2b815ef430b75cf539c5468eb84e6bf2c90e35", eol: "\r\n", ending: "" },
  "judges-ef2264d9.mjs": { sha: "3e257789867cdab1e997c291c327d3c7db7607303bd457df09b86dfe588fba9b", eol: "\n", ending: "\n" },
  "observations-4.json": { sha: "c357e3011f433ec87d97396cc644ca0ffea4b56fe76706a8d278cbd68bc32401", eol: "\r\n", ending: "" },
  "judges-b7824b6a.mjs": { sha: "33b92bfc698db917ef8de40b1f6295c296a5bb66e3cb8b29a2f851e4da968c3b", eol: "\n", ending: "\n" },
};
for (const [file, { sha, eol, ending }] of Object.entries(PINNED)) {
  const text = fs.readFileSync(path.join(HERE, file), "utf8").replace(/\r\n/g, "\n").replace(/\n+$/, "");
  const got = crypto.createHash("sha256").update(text.split("\n").join(eol) + ending.replace(/\n/g, eol)).digest("hex");
  if (got !== sha) { console.error(`${file}: sha256 ${got}, not the pinned ${sha}`); process.exit(2); }
}
// The judges are pure: anything that would reach a process or a socket throws.
const deny = (what) => () => { throw new Error(`replay: a judge reached ${what}`); };
for (const k of ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]) cp[k] = deny(k);
for (const k of ["connect", "createConnection"]) net[k] = deny(k);
globalThis.fetch = deny("fetch");

const judgesAt = async (...parts) => (await import(pathToFileURL(path.join(HERE, ...parts)).href)).JUDGES;
const NEW = await judgesAt("..", "..", "..", "..", "scripts", "sandbox", "gate", "judges.mjs");
const read = (file) => JSON.parse(fs.readFileSync(path.join(HERE, file), "utf8"));
const T = Date.parse("2026-10-21T00:00:00Z");
const iso = (t) => new Date(T + t).toISOString();
const word = (pass) => (pass ? "PASS" : "FAIL");

/** Round 1. The review's oracle ("MUST PASS"/"MUST FAIL"; its controls keep their recorded verdict). */
const ORACLE_1 = {
  "lawful-release-false-red": "PASS",
  "omitted-lawful-receipt-incomplete-model": "PASS",
  "early-held-claim-negative-control": "FAIL",
  "resume-after-idle-healthy": "PASS",
  "resume-Q-before-idle-false-green": "FAIL",
  "resume-pre-fresh-negative-control": "FAIL",
  "stop-empty-drain-false-green": "FAIL",
  "stop-named-undrained-negative": "FAIL",
  "stop-named-drained-healthy": "PASS",
  "last-known-negation-false-green": "FAIL",
};
/** Marks added for the successor's interface, by judge, with the reason (amendments A6, A7 and review R2). */
const PRIOR = { status: "working", appliedAt: iso(-10_000) };
const ADAPT = {
  "env-resumed": [() => ({ resumedAt: T + 29_000 }),
    "resumedAt: the successor bounds the fresh acceptance after the resume (R2); the review's history opens at 29 s"],
  "stop-restart": [(m) => ({ seedRunId: m.queuedAtStart?.[0] ?? null, seedStatusAtStop: m.queuedAtStart?.length ? "queued" : null,
    restartAccepted: { at: T + 50_000, seenAt: T + 50_000 }, wordAtRestart: m.wordAfter, wordAfterFirstTurn: m.wordAfter }),
  "A6: the review's queuedAtStart names the seed; its restart is the 50 s snapshot; its wordAfter is read at both checkpoints"],
  "restart-mid-turn": [() => ({ prior: PRIOR }),
    "A7: the prior record the scenario reads before the restart, which the note is compared against"],
};
const adapt = (row) => {
  const [add, why] = ADAPT[row.judge] ?? [];
  return add ? { evidence: { ...row.evidence, marks: { ...row.evidence.marks, ...add(row.evidence.marks) } }, why } : { evidence: row.evidence, why: null };
};
const round1 = read("observations.json").rows.map((r) => ({ ...r, oracle: ORACLE_1[r.name], recorded: word(r.actual.pass), ...adapt(r) }));
const lastKnown = round1.find((r) => r.name === "last-known-negation-false-green");
round1.push({ name: "last-known-values-matched-control", judge: "restart-mid-turn", oracle: "PASS", recorded: null,
  why: "a matched control, built here", evidence: { ...lastKnown.evidence, marks: { ...lastKnown.evidence.marks, prior: PRIOR,
    word: { status: "unknown", lastKnown: { ...PRIOR } } } } });

/** Round 2. Its rows carry their own oracle (`want`). The four A6 rows are not graded: the review withdrew their premise
 *  (INTERPRETATION-CORRECTION-A6.json: a fresh lifetime publishes busy as a boolean, never null). */
const round2 = read("observations-2.json").observations.filter((r) => !r.name.startsWith("A6-"))
  .map((r) => ({ name: r.name, judge: r.judge, oracle: word(r.want), recorded: word(r.actual.pass), evidence: r.evidence, why: null }));
// The review's straddling history without the watcher's sight of the run still queued after the hold: no millisecond
// observation orders the claim against the hold's end, so the answer must be UNKNOWN, not a breach and not a pass.
const straddle = round2.find((r) => r.name === "lawful-release-same-second-as-window-end").evidence;
// Round 3 (F1.a) showed an emitted `t` is no proof of when a frame was read, so the same-second row's PASS needs its
// 20.950 s queued frame to have been READ after the 20.900 s end. Verbatim, without acquisition stamps, it reads UNKNOWN.
Object.assign(round2.find((r) => r.name === "lawful-release-same-second-as-window-end"), {
  why: "F1.a: the review's 20.950 s queued frame as a read acquired at 20.920 s, after the end",
  evidence: { ...straddle, timeline: straddle.timeline.map((s) => (s.t === T + 20_950 ? { ...s, acquiredFrom: T + 20_920 } : s)) } });
round2.push({ name: "same-second-without-millisecond-evidence", judge: "legacy-idle-env-busy", oracle: "UNKNOWN", recorded: null,
  why: "a matched control, built here: the review's same-second row less its 20.950 s queued snapshot",
  evidence: { ...straddle, timeline: straddle.timeline.filter((s) => s.t !== T + 20_950) } });

/** Round 3. Its rows carry their own typed oracle (`want`) and the verdict ef2264d9 gave (`actual.verdict`). The
 *  review's frames carry no acquisition stamps, so its post-end empty inventory is ADAPTED: the 20.950 s frame gets the
 *  `acquiredFrom` its "successful post-end" description says it has (20.920 s, after the 20.900 s end). */
const round3 = read("observations-3.json").observations.map((r) => {
  const row = { name: r.name, judge: r.judge, oracle: r.want, recorded: r.actual.verdict, evidence: r.evidence, why: null };
  if (r.name !== "control-positive-empty-after-end-control") return row;
  return { ...row, why: "the review's successful post-end empty inventory (emitted 20.950 s) read from 20.920 s",
    evidence: { ...r.evidence, timeline: r.evidence.timeline.map((s) => (s.t === T + 20_950 ? { ...s, acquiredFrom: T + 20_920 } : s)) } };
});

/** Round 4. Its rows carry their typed oracle (`want`) and the verdict b7824b6a gave (`actual.verdict`), as used. */
const round4 = read("observations-4.json").rows.map((r) => ({ name: r.name, judge: "stale-second-holder", oracle: r.want,
  recorded: r.actual.verdict, evidence: r.evidence, why: null }));

let failures = 0;
const verdictOf = (v) => v.verdict ?? word(v.pass);
const show = (v) => `${verdictOf(v)}${v.failed.length || v.unknown?.length ? ` [${[...v.failed, ...(v.unknown ?? [])].join("; ")}]` : ""}`;
const check = (ok, text) => { if (!ok) failures += 1; console.log(`  ${ok ? "ok  " : "BAD "} ${text}`); };
for (const [title, judgesFile, rows, originals] of [
  ["round 1, sw3a-source-1792542290852", "judges-88efbf12.mjs", round1, read("observations.json").rows],
  ["round 2, sw3a-successor-1792548943247", "judges-5f4effad.mjs", round2, read("observations-2.json").observations],
  ["round 3, sw3a-f1-1792551632225", "judges-ef2264d9.mjs", round3, read("observations-3.json").observations],
  ["round 4, sw3a-acquisition-1792553532164", "judges-b7824b6a.mjs", round4, read("observations-4.json").rows]]) {
  const OLD = await judgesAt(judgesFile);
  const old = judgesFile.slice(7, 15);
  console.log(`== ${title}: ${old} against this commit`);
  for (const row of rows) {
    console.log(`${row.name} (${row.judge})${row.why ? `\n  adapted: ${row.why}` : ""}`);
    const verbatim = originals.find((r) => r.name === row.name)?.evidence;
    if (row.recorded !== null) {
      const before = OLD[row.judge](verbatim);
      check(verdictOf(before) === row.recorded, `${old}, verbatim: ${show(before)}; the review recorded ${row.recorded}`);
      const adapted = OLD[row.judge](row.evidence);
      check(verdictOf(adapted) === verdictOf(before), `${old}, adapted: ${show(adapted)}`);
      if (row.why) console.log(`  info this commit, verbatim: ${show(NEW[row.judge](verbatim))}`);
    } else console.log(`  info ${old}: ${show(OLD[row.judge](row.evidence))}`);
    const got = NEW[row.judge](row.evidence);
    check(verdictOf(got) === row.oracle, `this commit: ${show(got)}; the oracle is ${row.oracle}`);
    if (row.recorded !== null && row.recorded !== row.oracle) {
      console.log(`  -> a review defect: RED on ${old}, ${verdictOf(got) === row.oracle ? "GREEN" : "STILL RED"} after`);
    }
  }
}
console.log(failures ? `${failures} check(s) did not hold` : "every check held");
process.exit(failures ? 1 : 0);
