// The claude turn-END detector's state, and the three operations that are the only way to reach it.
//
// WHAT THE DETECTOR IS FOR. The claude Stop hook is not a guaranteed turn terminator — it misses on
// interrupt, on MCP continuations, on a crash, and when its short-timeout curl fails. A missed Stop leaves
// an agent `turn_busy=1` with no event able to clear it, which reads as `working` until the long ceiling.
// This detector is the backstop, and arming it is what turns accurate status on.
//
// LATE ARMING IS THE POINT, and the reason the state is mutable at all. A session launched without
// `--aify-agent` has no launch identity; it learns one at `comms_register`, and the detector must arm THEN.
// It used to arm only at module load, so registering silently failed to turn status on — the
// general-manager incident. Everything below is about that: arm once, arm late, never arm twice.
//
// Arming needs a claude-code runtime adapter, which this process does not have, so the tests exercise the
// refusal paths in-process and the successful path in child processes with `AIFY_RUNTIME=claude-code`.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "claude-turn-detector-state.mjs")).href;

const mod = await import("../claude-turn-detector-state.mjs");

// Arm in a child process with a chosen runtime, and report what happened.
function armIn({ runtime = "", agentId = "agent-a", second = null } = {}) {
  const script =
    "const m = await import(" + JSON.stringify(LEAF) + ");"
    + " const first = m.armClaudeTurnEndDetector(" + JSON.stringify(agentId) + ");"
    + " const armedAfterFirst = m.isClaudeTurnDetectorArmed();"
    + (second === null ? " const secondCall = null;"
      : " const secondCall = m.armClaudeTurnEndDetector(" + JSON.stringify(second) + ");")
    + " m.stopClaudeTurnEndDetector();"
    + " process.stdout.write(JSON.stringify({ first, armedAfterFirst, secondCall }));";
  return JSON.parse(execFileSync(
    process.execPath, ["--input-type=module", "-e", script],
    {
      env: { ...process.env, AIFY_RUNTIME: runtime, AIFY_AGENT_ID: "", AIFY_SERVER_URL: "" },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
    },
  ));
}

test("the module exposes three operations and no state", () => {
  // The reviewer's condition. A raw handle leaving the module would let a caller replace the stopper, and
  // the detector would keep running with nothing able to stop it.
  assert.deepEqual(Object.keys(mod).sort(),
    ["armClaudeTurnEndDetector", "isClaudeTurnDetectorArmed", "stopClaudeTurnEndDetector"]);
  const src = readFileSync(path.join(STDIO, "claude-turn-detector-state.mjs"), "utf-8");
  for (const name of ["__effectiveAgentId", "__claudeTurnDetectorArmed", "__stopClaudeTurnEndDetector"]) {
    assert.doesNotMatch(src, new RegExp(`^export\\s+let\\s+${name}\\b`, "m"), `${name} must stay private`);
  }
});

test("stopping an unarmed detector is safe", () => {
  // `cleanupOnExit` calls it unconditionally on every exit path, including a bridge that never armed. The
  // no-op default is what makes that safe, and it must survive becoming a wrapped operation.
  assert.equal(mod.isClaudeTurnDetectorArmed(), false, "nothing armed in this process");
  assert.doesNotThrow(() => mod.stopClaudeTurnEndDetector());
  assert.doesNotThrow(() => mod.stopClaudeTurnEndDetector(), "…and twice");
});

test("it REFUSES to arm without an agent id", () => {
  // An empty id is the pre-registration state. Arming then would start a detector that posts turn events for
  // nobody.
  for (const id of ["", "   ", null, undefined]) {
    assert.equal(mod.armClaudeTurnEndDetector(id), false, `${JSON.stringify(id)} must not arm`);
    assert.equal(mod.isClaudeTurnDetectorArmed(), false, "…and must leave it unarmed");
  }
});

test("it REFUSES to arm on a non-claude runtime", () => {
  // This detector reads a claude transcript. On codex or hermes there is nothing for it to read, and their
  // own detectors cover them — arming here would be a second turn-end source for one agent.
  for (const runtime of ["codex", "hermes", "pi", ""]) {
    const r = armIn({ runtime });
    assert.equal(r.first, false, `${runtime || "(no runtime)"} must not arm the claude detector`);
    assert.equal(r.armedAfterFirst, false);
  }
});

test("it ARMS on claude-code with an id — the late-arming path that fixes registration", () => {
  const r = armIn({ runtime: "claude-code", agentId: "late-registered-agent" });
  assert.equal(r.first, true, "arming must succeed once the runtime and an id are both present");
  assert.equal(r.armedAfterFirst, true, "…and it must report itself armed afterwards");
});

test("ARMING IS ONCE-ONLY: a second call is refused, not a second detector", () => {
  // The property the `armed` flag exists for. Two detectors on one transcript would both post /turn-end for
  // the same turn, and the second would fire against an already-cleared state.
  const r = armIn({ runtime: "claude-code", agentId: "agent-a", second: "agent-b" });
  assert.equal(r.first, true);
  assert.equal(r.secondCall, false, "the second arm must be refused");
  assert.equal(r.armedAfterFirst, true);
});

test("exactly one module declares each piece of state", () => {
  for (const name of ["__effectiveAgentId", "__claudeTurnDetectorArmed", "__stopClaudeTurnEndDetector"]) {
    assert.deepEqual(
      declaringModules(name), [{ file: "claude-turn-detector-state.mjs", kind: "binding" }],
      `${name} must be declared exactly once, by its owner`,
    );
  }
});

test("server.js reaches it only through the operations", () => {
  // The whole point of Option A. If server.js still touched a raw name, the state would have two owners and
  // the arming guard would be bypassable.
  const server = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["__effectiveAgentId", "__claudeTurnDetectorArmed", "__stopClaudeTurnEndDetector"]) {
    assert.doesNotMatch(server, new RegExp(`(?<![\\w.])${name}(?![\\w])`), `${name} must not appear in server.js`);
  }
  assert.match(server, /(?<![\w.])armClaudeTurnEndDetector\(/, "server.js must still arm it");
  assert.match(server, /(?<![\w.])stopClaudeTurnEndDetector\(\)/, "…and stop it on exit");
  assert.match(server, /(?<![\w.])isClaudeTurnDetectorArmed\(\)/, "…and check before re-arming");
});

test("the owner reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO, "claude-turn-detector-state.mjs"), "utf-8");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    "./aify-service-endpoint.mjs",
    "./bridge-instance.mjs",
    "./claude-turn-end-detector.js",
    "./launch-identity.mjs",
    "./runtime-adapter.mjs",
  ]);
  // The server-URL fix landed first precisely so this module would not need `__serverUrl`.
  assert.doesNotMatch(src, /__serverUrl/, "no duplicate server-URL derivation may enter a new module");
});
