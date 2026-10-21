// The receipt attempt's retained evidence (decision2-red/<scenario>/evidence.json), judged again by this commit's
// judges and compared with the verdicts and reasons the attempt recorded (attempt.json). The attempt ran the judges of
// 5f4effad; this shows whether the F1 repair moves any of its verdicts.
//
//   node docs/evidence/2026-10-21-sw3a-gate/rejudge.mjs > rejudge.txt
//
// Exit 0 when every case's verdict and reasons are identical, 1 when any differs.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CASES } from "../../../scripts/sandbox/gate/cases.mjs";
import { JUDGES } from "../../../scripts/sandbox/gate/judges.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const recorded = JSON.parse(fs.readFileSync(path.join(HERE, "attempt.json"), "utf8")).results;
let differs = 0;
for (const c of CASES) {
  const evidence = JSON.parse(fs.readFileSync(path.join(HERE, "decision2-red", c.scenario, "evidence.json"), "utf8"));
  const now = JUDGES[c.id](evidence);
  const then = recorded.find((r) => r.id === c.id);
  const same = now.pass === then.pass && JSON.stringify(now.failed) === JSON.stringify(then.failed) && now.unknown.length === 0;
  if (!same) differs += 1;
  console.log(`${c.id}: ${now.verdict}, ${same ? "identical to the recorded verdict" : `DIFFERS from the recorded ${then.pass ? "PASS" : "FAIL"}`}`
    + `${[...now.failed, ...now.unknown].length ? ` [${[...now.failed, ...now.unknown].join("; ")}]` : ""}`);
}
console.log(`${CASES.length - differs}/${CASES.length} cases identical`);
process.exit(differs ? 1 : 0);
