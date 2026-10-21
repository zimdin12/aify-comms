// A gate attempt runs inside the runner's primary window (runner review R2, carried into the gate; SW3a review of
// 11d8f3ae): a command running when the attempt budget passes is cut there, cleanup still runs in full, and a verdict
// reached after the budget is refused. The attempt's real code runs; only Docker is a stand-in, and every stand-in
// call goes through the runner's own run() as a node child, so the window acts on it as it acts on docker.
//
//   node --test scripts/tests/gate_attempt_window.mjs        (also run by scripts/tests/test_gate_attempt_window.py)

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.AIFY_ENV_REPO = REPO; // any repo whose HEAD resolves; nothing is built
process.env.SANDBOX_GATE_IMAGES = "stand-in-comms,stand-in-env";
const { attempt } = await import("../sandbox/gate/gate.mjs");
const { run } = await import("../sandbox/env-against-comms.mjs");
const { CASES } = await import("../sandbox/gate/cases.mjs");

const CHILD = "const a = JSON.parse(process.argv[1]); setTimeout(() => { process.stdout.write(a.out); process.stderr.write(a.err); process.exit(a.status); }, a.ms)";
const NOT_FOUND = { container: (r) => `No such container: ${r}`, network: (r) => `network ${r} not found` };

/** A stand-in Docker: creates and removes by name, answers inspect as the daemon does, refuses everything else. */
function standIn({ slowMs }) {
  const live = new Map(); // name -> id
  const calls = [];
  const decide = (args) => {
    const ok = (out = "") => ({ status: 0, out, err: "", ms: 0 });
    const own = (name) => { const id = (live.size + 1).toString(16).padStart(64, "0"); live.set(name, id); return ok(`${id}\n`); };
    if (args[1] === "inspect" && args[3] === "{{.Id}}") {
      const id = live.get(args[4]) ?? [...live.values()].find((v) => v === args[4]);
      return id ? ok(`${id}\n`) : { status: 1, out: "", err: `Error response from daemon: ${NOT_FOUND[args[0]](args[4])}\n`, ms: 0 };
    }
    if (args[0] === "network" && args[1] === "create") return own(args.at(-1));
    if (args[0] === "run") {
      const name = args[args.indexOf("--name") + 1];
      return { ...own(name), ms: name.endsWith("-comms") ? slowMs : 0 };
    }
    if (args[0] === "inspect") return ok("\n"); // the declared volumes: none
    if (args[0] === "rm" || (args[0] === "network" && args[1] === "rm")) {
      for (const [name, id] of live) if (name === args.at(-1) || id === args.at(-1)) live.delete(name);
      return ok();
    }
    return { status: 1, out: "", err: `stand-in: ${args[0]} is not scripted\n`, ms: 0 };
  };
  const dock = (args, opts) => {
    const from = Date.now();
    const result = run(process.execPath, ["-e", CHILD, JSON.stringify(decide(args))], opts);
    calls.push({ args, result, ms: Date.now() - from });
    return result;
  };
  return { dock, calls, live };
}

async function attemptWith({ slowMs, budgetMs }) {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "gate-window-"));
  const docker = standIn({ slowMs });
  const result = await attempt({ commsRef: "HEAD", envRef: "HEAD", label: "window", only: [CASES[0].scenario], outDir,
    dock: docker.dock, budgetMs });
  const wrote = fs.existsSync(path.join(outDir, "attempt.json"));
  fs.rmSync(outDir, { recursive: true, force: true });
  return { result, wrote, ...docker };
}
const isCleanup = (args) => args[0] === "rm" || (args[0] === "network" && args[1] === "rm");

// One attempt whose budget passes while comms is starting; each obligation is its own test, so a failure names it.
let overrun;
const overrunAttempt = () => (overrun ??= attemptWith({ slowMs: 20_000, budgetMs: 4_000 }));

test("the window cuts a command running when the attempt budget passes", async () => {
  const { calls } = await overrunAttempt();
  const comms = calls.find((c) => c.args[0] === "run" && c.args[c.args.indexOf("--name") + 1].endsWith("-comms"));
  assert.ok(comms, "the comms container was started");
  assert.ok(comms.result.timedOut && comms.ms < 12_000, `cut at the budget, not at its own 20 s (${comms.ms} ms)`);
});

test("cleanup after the budget passed runs in full", async () => {
  const { result, calls, live } = await overrunAttempt();
  const removals = calls.filter((c) => isCleanup(c.args));
  assert.ok(removals.length > 0, "cleanup removed something");
  assert.deepEqual(removals.filter((c) => c.result.status !== 0).map((c) => c.result.out), [], "no cleanup command was refused");
  assert.deepEqual([...live.keys()], [], "nothing the attempt made is left");
  assert.match(result.lines[1], /^cleanup: complete/);
});

test("a verdict reached after the attempt budget is refused", async () => {
  const { result, wrote } = await overrunAttempt();
  assert.equal(result.lines[0], "primary: ERROR: the attempt deadline passed");
  assert.equal(wrote, false, "no attempt.json for a refused verdict");
});

test("control: inside the budget nothing is cut or refused, and the verdict is the cases'", async () => {
  const { result, wrote, calls, live } = await attemptWith({ slowMs: 0, budgetMs: 120_000 });
  assert.deepEqual(calls.filter((c) => c.result.timedOut).map((c) => c.args.join(" ")), [], "no call timed out or was refused");
  assert.match(result.lines[0], /^primary: 0\/\d+ cases passed$/);
  assert.match(result.lines[1], /^cleanup: complete/);
  assert.deepEqual([...live.keys()], []);
  assert.equal(wrote, true);
});
