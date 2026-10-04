// Each mutant undoes one decision in lib/agent-state-host.mjs; the named test must fail. Fail-closed: a mutant counts
// only when node --test exits non-zero AND the named test is among the `not ok` lines; any survivor exits 1.
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = process.argv[2];
const FILE = path.join(ROOT, "lib/agent-state-host.mjs");
const TEST = "tests/agent-state-host.test.js";
const good = fs.readFileSync(FILE);
const text = good.toString("utf8");
const digest = (b) => crypto.createHash("sha256").update(b).digest("hex");
const once = (from, to) => (s) => {
  if (s.split(from).length !== 2) throw new Error(`site not unique: ${from.slice(0, 60)}`);
  return s.replace(from, to);
};

const MUTANTS = [
  ["held gate removed", once(
    'if (current && !this.#known.has(current.lifetime) && cause === "at-prompt")', "if (false)"),
    "FIRST ADOPTION WITH NO STORED TURN"],
  ["unreadable turns file marks every lifetime known", once(
    '      problems.push(`turns: ${stored.problem}`);\n',
    '      problems.push(`turns: ${stored.problem}`);\n      for (const r of this.#verdicts) this.#known.add(r.record.lifetime);\n'),
    "AN UNREADABLE TURNS FILE RESTORES NOTHING"],
  ["a proved-dead record is kept", once(
    'if (judged.verified === "no" && record.instance === this.#instance) this.#remove(record, problems);', ""),
    "A REUSED PID"],
  ["an applied event is not saved", once("      writeTurns(this.#turnsPath, result.turns);\n", ""),
    "AN APPLIED EVENT IS DURABLE"],
  ["restore treats an unknown lifetime as gone", once(
    '(lifetime) => verdicts.get(lifetime) ?? "unknown"', '(lifetime) => verdicts.get(lifetime) === "yes" ? "yes" : "no"'),
    "A RESTART RESTORES EACH STORED TURN"],
  ["a conflict reads running", once("conflict: Boolean(entry?.conflict),", "conflict: false,"),
    "TWO VERIFIED LIFETIMES ARE A CONFLICT"],
  ["a retained turn is renewed", once("const strict = turnIsBusy(turn ?? undefined, { nowUs, renewable: false });",
    "const strict = turnIsBusy(turn ?? undefined, { nowUs, renewable: true });"),
    "A RETAINED TURN IS STRICT"],
];

const run = () => spawnSync(process.execPath, ["--test", TEST], { cwd: ROOT, encoding: "utf8" });
const survivors = [];
try {
  for (const [label, mutate, expected] of MUTANTS) {
    fs.writeFileSync(FILE, mutate(text));
    const r = run();
    const failed = r.stdout.split("\n").filter((l) => l.startsWith("not ok"));
    const killed = r.status !== 0 && failed.some((l) => l.includes(expected));
    if (!killed) survivors.push(label);
    console.log(`${killed ? "KILLED  " : "SURVIVED"} ${label}: exit=${r.status}; ${failed.map((l) => l.slice(0, 90)).join(" | ") || "nothing failed"}`);
    fs.writeFileSync(FILE, good);
  }
} finally {
  fs.writeFileSync(FILE, good);
}
if (digest(fs.readFileSync(FILE)) !== digest(good)) throw new Error("not restored");
const restored = run();
console.log(`restored: exit=${restored.status} ${restored.stdout.split("\n").filter((l) => /^# (pass|fail)/.test(l)).join(" ")}`);
if (survivors.length || restored.status !== 0) { console.log(`FAILED: ${survivors.join(", ")}`); process.exit(1); }
console.log(`all ${MUTANTS.length} mutants killed at their named test`);
