#!/usr/bin/env node
// Exercises hermes-model-probe.mjs's PATH cleanup in PROCESS scope: the persistent User PATH is never
// read or written. The command is extracted from the probe's own text, so this checks what the probe runs.
// Cases from comms-senior-dev's review of 10be3da6: a sibling sharing the home's prefix must survive, and
// a forward-slash home must still match a backslash entry.
//   node docs/superpowers/plans/evidence/2026-10-02-integration/check-probe-path-cleanup.mjs [--no-cleanup]
// --no-cleanup runs the same cases with the command replaced by nothing: the negative control, which
// must report the home as still present.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// --probe <file> checks another copy, e.g. 10be3da6's (`git show 10be3da6:<path> > old.mjs`), which must fail.
const at = process.argv.indexOf("--probe");
const probe = at > 0 ? process.argv[at + 1]
  : path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "2026-10-01-p6", "hermes-model-probe.mjs");
const m = /const DROP_HOME_FROM_USER_PATH =([\s\S]*?);\r?\n/.exec(fs.readFileSync(probe, "utf8"));
if (!m) { console.log("EXTRACTOR FOUND NOTHING"); process.exit(2); }
const command = process.argv.includes("--no-cleanup") ? "$null" : new Function(`return (${m[1]});`)();

const run = (home, seed) => {
  const r = spawnSync("powershell", ["-NoProfile", "-Command", `$env:Path=$env:SEED; ${command}; Write-Output $env:Path`],
    { env: { ...process.env, SEED: seed.join(";"), PROBE_HOME: home, PROBE_PATH_SCOPE: "Process" }, encoding: "utf8" });
  return { status: r.status, path: r.stdout.trim().split(/\r?\n/).pop().split(";") };
};

const HOME = String.raw`C:\Users\X\AppData\Local\Temp\hermes-model-probe-ab12`;
const KEEP = [String.raw`C:\keep\one`, String.raw`C:\Windows\System32\WindowsPowerShell\v1.0`];
const SIBLING = String.raw`C:\Users\X\AppData\Local\Temp\hermes-model-probe-ab12-sibling\bin`;
const cases = [
  ["its home's bin is removed", HOME, [`${HOME}\\bin`, ...KEEP], { gone: [`${HOME}\\bin`], kept: KEEP }],
  ["the home itself, with a trailing slash", HOME, [`${HOME}\\`, ...KEEP], { gone: [`${HOME}\\`], kept: KEEP }],
  ["a sibling sharing the prefix survives", HOME, [SIBLING, `${HOME}\\bin`, ...KEEP], { gone: [`${HOME}\\bin`], kept: [SIBLING, ...KEEP] }],
  ["a forward-slash home matches a backslash entry", HOME.replaceAll("\\", "/"), [`${HOME}\\bin`, ...KEEP], { gone: [`${HOME}\\bin`], kept: KEEP }],
  ["case differs", HOME.toUpperCase(), [`${HOME}\\bin`, ...KEEP], { gone: [`${HOME}\\bin`], kept: KEEP }],
  ["nothing of ours: nothing removed", HOME, [...KEEP], { gone: [], kept: KEEP }],
];
let failed = 0;
for (const [name, home, seed, { gone, kept }] of cases) {
  const { status, path: after } = run(home, seed);
  const stillThere = gone.filter((e) => after.includes(e));
  const lost = kept.filter((e) => !after.includes(e));
  const ok = status === 0 && !stillThere.length && !lost.length;
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: exit ${status}${stillThere.length ? `, still there ${stillThere}` : ""}${lost.length ? `, lost ${lost}` : ""}`);
}
process.exit(failed ? 1 : 0);
