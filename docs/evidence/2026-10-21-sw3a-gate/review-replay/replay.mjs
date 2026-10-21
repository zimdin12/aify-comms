// The review's own synthetic histories (sw3a-source-1792542290852, JUDGE-COHERENT-OBSERVATIONS.json, copied here as
// observations.json) replayed through the judges of 88efbf12 (judges-88efbf12.mjs, its blob) and of the successor.
//
//   node docs/evidence/2026-10-21-sw3a-gate/review-replay/replay.mjs > replay.txt
//
// Each row's evidence is used as the review wrote it. Where the successor's judge reads a mark the review's shape lacks,
// the row is ADAPTED by adding marks only (nothing removed or changed, so 88efbf12's judge reads the same input), and the
// adaptation is printed with its reason. Both judges run on the verbatim and the adapted evidence. The replay checks:
//   - 88efbf12 on the verbatim evidence gives the review's recorded verdict (the instrument reproduces the review);
//   - 88efbf12 gives the same verdict on the adapted evidence (the adaptation hides nothing from the old judge);
//   - the successor gives the review's oracle.
// Exit 0 only when every check holds.

import crypto from "node:crypto";
import cp from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Each copy is committed ending in one newline, as the repo's whitespace gate requires; the original's own ending is
// put back before hashing, so the pin still proves the copy is the original byte for byte.
const PINNED = {
  "observations.json": ["7a4c1c5681073ed7f63412dc3226d014b30fab65aba07df1b0622d06d23df418", ""],
  "judges-88efbf12.mjs": ["86ea74ddf7558eb24189063a4f343265993cfde61f39cbc95eb5a2510252cfea", "\n\n"],
};
for (const [file, [want, ending]] of Object.entries(PINNED)) {
  // Line endings are normalised first, so a checkout that converts them reads the same bytes.
  const text = fs.readFileSync(path.join(HERE, file), "utf8").replace(/\r\n/g, "\n").replace(/\n+$/, "") + ending;
  const got = crypto.createHash("sha256").update(text).digest("hex");
  if (got !== want) { console.error(`${file}: sha256 ${got}, not the pinned ${want}`); process.exit(2); }
}
// The judges are pure: anything that would reach a process or a socket throws.
const deny = (what) => () => { throw new Error(`replay: a judge reached ${what}`); };
for (const k of ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]) cp[k] = deny(k);
for (const k of ["connect", "createConnection"]) net[k] = deny(k);
globalThis.fetch = deny("fetch");

const OLD = (await import(pathToFileURL(path.join(HERE, "judges-88efbf12.mjs")).href)).JUDGES;
const NEW = (await import(pathToFileURL(path.join(HERE, "..", "..", "..", "..", "scripts", "sandbox", "gate", "judges.mjs")).href)).JUDGES;
const { rows } = JSON.parse(fs.readFileSync(path.join(HERE, "observations.json"), "utf8"));
const T = Date.parse("2026-10-21T00:00:00Z");
const iso = (t) => new Date(T + t).toISOString();

/** What the successor's judge must answer for each review row: the review's oracle ("MUST PASS"/"MUST FAIL", and its
 *  controls, which keep their recorded verdict). */
const ORACLE = {
  "lawful-release-false-red": true,
  "omitted-lawful-receipt-incomplete-model": true,
  "early-held-claim-negative-control": false,
  "resume-after-idle-healthy": true,
  "resume-Q-before-idle-false-green": false,
  "resume-pre-fresh-negative-control": false,
  "stop-empty-drain-false-green": false,
  "stop-named-undrained-negative": false,
  "stop-named-drained-healthy": true,
  "last-known-negation-false-green": false,
};

/** Marks added for the successor's interface, by judge, with the reason (amendments A6, A7 and review R2). */
const PRIOR = { status: "working", appliedAt: iso(-10_000) };
const ADAPT = {
  "env-resumed": [(m) => ({ resumedAt: T + 29_000 }),
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

/** A matched control beside the review's R8 row: the same history with the note carrying the prior's values passes. */
const lastKnown = rows.find((r) => r.name === "last-known-negation-false-green");
const controls = [{ name: "last-known-values-matched-control", judge: "restart-mid-turn", oracle: true, recorded: null,
  evidence: { ...lastKnown.evidence, marks: { ...lastKnown.evidence.marks, prior: PRIOR,
    word: { status: "unknown", lastKnown: { ...PRIOR } } } } }];

let failures = 0;
const verdict = (v) => `${v.pass ? "PASS" : "FAIL"}${v.failed.length ? ` [${v.failed.join("; ")}]` : ""}`;
const check = (ok, text) => { if (!ok) failures += 1; console.log(`  ${ok ? "ok  " : "BAD "} ${text}`); };
for (const row of [...rows.map((r) => ({ ...r, oracle: ORACLE[r.name], recorded: r.actual.pass })), ...controls]) {
  const { evidence, why } = row.recorded === null ? { evidence: row.evidence, why: "a matched control, built here" } : adapt(row);
  console.log(`${row.name} (${row.judge})${why ? `\n  adapted: ${why}` : ""}`);
  if (row.recorded !== null) {
    const old = OLD[row.judge](row.evidence);
    check(old.pass === row.recorded, `88efbf12, verbatim: ${verdict(old)}; the review recorded ${row.recorded ? "PASS" : "FAIL"}`);
    const oldAdapted = OLD[row.judge](evidence);
    check(oldAdapted.pass === old.pass, `88efbf12, adapted: ${verdict(oldAdapted)}`);
  }
  const got = NEW[row.judge](evidence);
  check(got.pass === row.oracle, `successor: ${verdict(got)}; the oracle is ${row.oracle ? "PASS" : "FAIL"}`);
  if (row.recorded !== null && row.recorded !== row.oracle) console.log(`  -> a review defect: RED on 88efbf12, ${got.pass === row.oracle ? "GREEN" : "STILL RED"} after`);
}
console.log(failures ? `${failures} check(s) did not hold` : "every check held");
process.exit(failures ? 1 : 0);
