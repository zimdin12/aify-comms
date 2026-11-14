// E5 plants: each removes one limb; the named test must go red. Foreground; restored by copy, compared by bytes.
import { copyFileSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";

const S = "scripts/staging/";
const PLANTS = [
  ["redaction of the dashboard key dropped", S + "session.mjs", "this.layout.dashboardKey ? redacted.replaceAll(this.layout.dashboardKey, '<dashboard-key>') : redacted", "redacted", "redact"],
  ["both-phases rule dropped", S + "manifest.mjs", "    if (('dashboard' in this.baseline) !== ('dashboard' in this.candidate)) throw new TypeError('dashboard must be staged in both phases or neither');\n", "", "one phase only"],
  ["dashboard ref not validated", S + "manifest.mjs", "  if (value.dashboard !== undefined) result.dashboard = value.dashboard;\n", "  const dashboard = value.dashboard;\n", "moving dashboard ref"],
  ["dashboard key may equal the fixture key", S + "layout.mjs", " || dashboardKey === fixtureKey)", ")", "run-owned-fixture-key"],
  ["empty dashboard key admitted", S + "layout.mjs", " || !dashboardKey.trim()", "", "\"  \""],
  ["dashboard volume unlabelled", S + "layout.mjs", "{ 'dashboard-data': { name: `${this.project}-dashboard-data`, labels: { ...labels } } }", "{ 'dashboard-data': { name: `${this.project}-dashboard-data` } }", "own run volume"],
  ["dashboard service always generated", S + "layout.mjs", "...(this.dashboardKey ? { 'aify-dashboard': {", "...(true ? { 'aify-dashboard': {", "without a dashboard key"],
  ["dashboard uses the fixture key", S + "layout.mjs", "API_KEY: this.dashboardKey,", "API_KEY: this.fixtureKey,", "its own key"],
  ["engine: an equal version refused", S + "scenarios/e5.mjs", "return order === -1 || have[order]", "return have[order]", "node v24.15.0 against engine >=24.15 is admitted"],
  ["engine: the comparison reversed", S + "scenarios/e5.mjs", "have[order] > want[order]", "have[order] < want[order]", "node v22.20.0 against engine >=24.15 is refused"],
  ["engine: a two-part range leaves its patch unset", S + "scenarios/e5.mjs", "Number(part ?? 0)", "Number(part)", "node v24.15.0 against engine >=24.15 is admitted"],
  ["engine: no declared engine admitted", S + "scenarios/e5.mjs", "if (!have || !want) return false;", "if (!have || !want) return true;", "engine null is refused"],
  ["engine: an unanchored version admitted", S + "scenarios/e5.mjs", "/^v(\\d+)", "/v?(\\d+)", "node 24.22.1 against engine >=24.15 is refused"],
  ["engine: a caret range admitted", S + "scenarios/e5.mjs", "/^>=\\s*(", "/^(?:>=|\\^)\\s*(", "engine ^24.15 is refused"],
  ["publication: a second managed publisher admitted", S + "scenarios/e5.mjs", "managed.length === 1 &&", "managed.length >= 1 &&", "two managed publishers is refused"],
  ["publication: any residents-named publisher admitted", S + "scenarios/e5.mjs", "publishers.filter(p => p.instance !== residentsInstance)", "publishers.filter(p => !p.instance.startsWith('residents-'))", "no residents role is held is refused"],
  ["publication: an agent from any publisher counts", S + "scenarios/e5.mjs", "ids.every(id => managed[0].agents.includes(id))", "ids.every(id => publishers.some(p => p.agents.includes(id)))", "only the residents publisher names is refused"],
  ["publication: freshness not required", S + "scenarios/e5.mjs", "\n    && publishers.every(p => p.fresh === true)", "", "a stale publisher is refused"],
  ["publication: conflict not read", S + "scenarios/e5.mjs", "a.agentId === id && a.conflict === false", "a.agentId === id", "an agent read as a conflict is refused"],
];
const TEST = "mcp/stdio/tests/staging-e5.test.js";
const run = () => spawnSync(process.execPath, ["--test", TEST], { encoding: "utf8" });
const named = (out) => [...out.matchAll(/^not ok \d+ - (.*)$/gm)].map((m) => m[1]);
const files = [...new Set(PLANTS.map((p) => p[1]))];
const saved = new Map(files.map((f) => [f, readFileSync(f)]));
for (const f of files) copyFileSync(f, `${f}.plant-orig`);
const base = run();
console.log(`baseline exit ${base.status}`);
if (base.status !== 0) { for (const f of files) rmSync(`${f}.plant-orig`); console.log("REFUSED: red baseline"); process.exit(2); }
let red = 0;
try {
  for (const [name, file, from, to, expect] of PLANTS) {
    const text = saved.get(file).toString("utf8");
    if (text.split(from).length !== 2) { console.log(`${name}: NEEDLE NOT UNIQUE`); continue; }
    writeFileSync(file, text.replace(from, to));
    const r = run();
    const fails = named(r.stdout);
    const hit = r.status !== 0 && fails.some((n) => n.includes(expect));
    if (hit) red += 1;
    console.log(`${name}: ${hit ? "RED" : r.status !== 0 ? "RED ELSEWHERE" : "STILL GREEN"} | ${fails.join(" | ")}`);
    copyFileSync(`${file}.plant-orig`, file);
  }
} finally {
  for (const f of files) {
    copyFileSync(`${f}.plant-orig`, f);
    rmSync(`${f}.plant-orig`);
    console.log(`${f} restored identical: ${Buffer.compare(readFileSync(f), saved.get(f)) === 0}`);
  }
}
console.log(`${red}/${PLANTS.length} red on the named test`);
process.exit(red === PLANTS.length ? 0 : 1);
