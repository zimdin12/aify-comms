// Each mutant undoes one decision in user-path.mjs; check-probe-path-cleanup.mjs must report FAIL for each.
//   node docs/superpowers/plans/evidence/2026-10-02-integration/check-probe-path-cleanup-mutants.mjs "$PWD"   (from the checkout root)
import fs from "node:fs";
import { spawnSync } from "node:child_process";

const ROOT = process.argv[2];
const FILE = `${ROOT}/docs/superpowers/plans/evidence/2026-10-01-p6/user-path.mjs`;
const CHECK = `${ROOT}/docs/superpowers/plans/evidence/2026-10-02-integration/check-probe-path-cleanup.mjs`;
const good = fs.readFileSync(FILE, "utf8");
const mutants = [
  ["P1 presence test folds slashes", 'return String(entry).replace(/[\\\\/]+$/, "").toLowerCase();',
    'return String(entry).replaceAll("/", "\\\\").replace(/[\\\\/]+$/, "").toLowerCase();'],
  ["P2 no profile-home refusal", "if (/\\\\profiles\\\\[^\\\\]+$/i.test(resolved))", "if (false)"],
  ["P3 safety net off", "return Boolean(first) && !String(beforeRaw).split(\";\").includes(first);", "return false;"],
];
try {
  for (const [name, from, to] of mutants) {
    if (good.split(from).length !== 2) { console.log(`${name}: SITE NOT FOUND`); continue; }
    fs.writeFileSync(FILE, good.replace(from, to));
    const r = spawnSync(process.execPath, [CHECK], { encoding: "utf8" });
    const fails = r.stdout.split("\n").filter((l) => l.startsWith("FAIL")).map((l) => l.slice(5, 75));
    console.log(`${name}: ${fails.length ? "KILLED" : "SURVIVED"} ${fails.join(" | ")}`);
    fs.writeFileSync(FILE, good);
  }
} finally {
  fs.writeFileSync(FILE, good);
}
