// The gate's Pair reads Docker's stdout as data, never stdout and stderr together (runner review R1, carried into
// scripts/sandbox/gate/pair.mjs). A scripted stand-in Docker answers every call with the value on stdout and a benign
// warning on stderr, shaped exactly as the runner's run() returns it ({status, stdout, stderr, out = stdout+stderr}).
// Each of pair.mjs's nine data reads must produce exactly the stdout value.
//
//   node --test scripts/tests/gate_pair_stdout.mjs        (also run by scripts/tests/test_gate_pair_stdout.py)

import assert from "node:assert/strict";
import test from "node:test";
import { Pair } from "../sandbox/gate/pair.mjs";

const WARNING = "WARNING: a benign line on stderr\n";
const ID = "a".repeat(64);
const NOT_FOUND = { container: (r) => `No such container: ${r}`, network: (r) => `network ${r} not found` };

/** A stand-in for the runner's docker(): `answers(args)` gives stdout (or a full result); stderr carries WARNING. */
function standIn(answers) {
  const calls = [];
  const created = new Set();
  const dock = (args) => {
    calls.push(args);
    // The runner's exists() and create()'s binding: `<kind> inspect --format {{.Id}} <name>`.
    if (args[1] === "inspect" && args[3] === "{{.Id}}" && NOT_FOUND[args[0]]) {
      return created.has(args[4]) ? result(`${ID}\n`)
        : { status: 1, stdout: "", stderr: `Error response from daemon: ${NOT_FOUND[args[0]](args[4])}\n`, out: "" };
    }
    if (args[0] === "network" && args[1] === "create") { created.add(args.at(-1)); return result(`${ID}\n`); }
    if (args[0] === "run") { created.add(args[args.indexOf("--name") + 1]); return result(`${ID}\n`); }
    const answer = answers(args);
    return typeof answer === "object" && answer !== null ? answer : result(answer ?? "");
  };
  return { dock, calls };
}
const result = (stdout, status = 0) => ({ status, stdout, stderr: WARNING, out: `${stdout}${WARNING}` });

const pairWith = (answers) => {
  const { dock, calls } = standIn(answers);
  const owned = [];
  return { pair: new Pair({ owned, runId: "t", caseId: "s0", images: { comms: "c", env: "e" }, probePath: "probe.py",
    key: "k", dock }), owned, calls };
};
const has = (args, ...words) => words.every((w) => args.includes(w));

test("snapshot: the probe's last stdout line, not the warning after it", () => {
  const { pair } = pairWith((a) => has(a, "snapshot") ? '{"t":1}\n' : "");
  assert.deepEqual(pair.snapshot(), { t: 1 });
});

test("api: status and JSON body from stdout", () => {
  const { pair } = pairWith((a) => has(a, "api") ? '201 {"ok":true}\n' : "");
  const r = pair.api("POST", "/x", {});
  assert.equal(r.status, 201);
  assert.deepEqual(r.json, { ok: true });
});

test("timeline: every stdout line is a frame, and no warning line is parsed", () => {
  const { pair } = pairWith((a) => has(a, "/tmp/timeline.jsonl") ? '{"t":1}\n{"t":2}\n' : "");
  assert.deepEqual(pair.timeline(), [{ t: 1 }, { t: 2 }]);
});

test("standin: a last line with no newline is not glued to the warning", () => {
  const { pair } = pairWith((a) => has(a, "cat") && a.some((x) => String(x).endsWith(".log")) ? '{"e":"claim"}' : "");
  assert.deepEqual(pair.standin(), [{ e: "claim" }]);
});

test("lifetime: the file's JSON, not the file and the warning", () => {
  const { pair } = pairWith((a) => a.some((x) => String(x).endsWith(".lifetime.json")) ? '{"lifetime":"L1"}' : "");
  assert.equal(pair.lifetime(), "L1");
});

test("contact: the relay log's lines, its last line not glued to the warning", () => {
  const { pair } = pairWith((a) => has(a, "/tmp/relay/log.jsonl") ? '{"event":"report"}' : "");
  assert.deepEqual(pair.contact(), [{ event: "report" }]);
});

test("the env daemon pid: one pid from stdout, so pausing it names that pid", () => {
  const { pair, calls } = pairWith((a) => has(a, "sh", "-c") && String(a.at(-1)).includes("/proc/") ? "123\n" : "");
  pair.pauseEnv();
  assert.ok(calls.some((a) => String(a.at(-1)) === "kill -STOP 123"), "paused pid 123");
});

test("restart: the start time is stdout's timestamp", async () => {
  const { pair } = pairWith((a) => has(a, "{{.State.StartedAt}}") ? "2026-10-21T01:00:00.5Z\n" : "");
  assert.equal(await pair.restartComms(), Date.parse("2026-10-21T01:00:00.5Z"));
});

test("start: the volumes owned are the names on stdout, not words of the warning", async () => {
  const { pair, owned } = pairWith((a) => {
    if (has(a, "inspect") && String(a[2]).includes(".Mounts")) return "volA volB \n";
    if (has(a, "/tmp/relay/log.jsonl")) return '{"event":"listening"}\n';
    if (a[0] === "logs") return "listening on 1\n";
    if (has(a, "api")) return '200 {"ok":true}\n';
    return "";
  });
  await pair.start();
  const volumes = owned.filter((o) => o.kind === "volume").map((o) => o.ref);
  assert.deepEqual(volumes, ["volA", "volB", "volA", "volB"]);
});
