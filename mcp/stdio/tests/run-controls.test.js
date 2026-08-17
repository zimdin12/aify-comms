// Applying out-of-band controls to a run that is already executing.
//
// The two properties worth a test are the ones that are silent when they break: steers must be BATCHED into
// one interruption, and every claimed control must be ANSWERED. A control the bridge claims and never
// PATCHes back stays claimed forever — the operator's stop button reports nothing and the queue never
// drains — and that failure produces no error anywhere.
//
// The fake service runs INSIDE the child and binds 127.0.0.2. `execFileSync` blocks the parent's event loop
// so a parent-hosted server cannot answer, and `defaultFallbackServerUrls` adds the real `127.0.0.1:8800`
// as a fallback for any loopback primary — which is how an earlier test in this repo posted to the
// operator's live service.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "run-controls.mjs")).href;

// Claim the given controls, run the real function against a controller with the given capabilities, and
// report what the controller saw and what was PATCHed back for each control.
function apply({ controls, capabilities = { steer: true, interrupt: true }, steerThrows = null,
  interruptThrows = null, activeRun = {} } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "aify-rc-test-"));
  const script = `
    import http from "node:http";
    const patches = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        if (req.method === "POST" && req.url.endsWith("/dispatch/controls/claim")) {
          res.writeHead(200, { "content-type": "application/json" });
          return res.end(JSON.stringify({ controls: ${JSON.stringify(controls)} }));
        }
        if (req.method === "PATCH" && req.url.includes("/dispatch/controls/")) {
          patches.push({ id: decodeURIComponent(req.url.split("/").pop()), ...JSON.parse(body || "{}") });
          res.writeHead(200, { "content-type": "application/json" });
          return res.end("{}");
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;
    process.env.CLAUDE_MCP_SERVER_URL = "";

    const { processRunControls } = await import(${JSON.stringify(LEAF)});
    const steers = []; let interrupts = 0;
    const controller = {
      capabilities: ${JSON.stringify(capabilities)},
      steer: ${capabilities.steer ? `async (b) => { steers.push(b); ${steerThrows ? `throw new Error(${JSON.stringify(steerThrows)});` : ""} }` : "undefined"},
      interrupt: ${capabilities.interrupt ? `async () => { interrupts += 1; ${interruptThrows ? `throw new Error(${JSON.stringify(interruptThrows)});` : ""} }` : "undefined"},
    };
    const active = { runId: "run-1", controller, ...${JSON.stringify(activeRun)} };
    let threw = null;
    try { await processRunControls("agent-a", active); } catch (e) { threw = String(e?.message || e); }
    srv.close();
    process.stdout.write(JSON.stringify({ steers, interrupts, patches, threw }));
  `;
  try {
    return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
      env: {
        ...sealedChildEnv(), AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "",
        TEMP: home, TMP: home, XDG_STATE_HOME: path.join(home, "state"),
        AIFY_AGENT_ID: "rc-test-agent", AIFY_HERMES_GATEWAY_URL: "",
      },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
    }));
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
}

const steer = (id, body) => ({ id, action: "steer", body });

test("FOUR QUEUED STEERS BECOME ONE INTERRUPTION, not four", () => {
  // The property the batching exists for. Steering interrupts the model's turn to inject text, so applying
  // them separately would disrupt the turn once per message and deliver four unrelated interjections.
  const r = apply({ controls: [steer("c1", "one"), steer("c2", "two"), steer("c3", "three"), steer("c4", "four")] });
  assert.equal(r.steers.length, 1, "four steers must reach the controller as ONE steer");
  const [body] = r.steers;
  assert.match(body, /\[AIFY STEER BATCH\]/, "…carrying the envelope that says it is a batch");
  assert.match(body, /4 messages arrived/, "…naming how many, so the agent knows what it is reading");
  assert.match(body, /apply them to the current turn in order/i);
  for (const text of ["one", "two", "three", "four"]) {
    assert.ok(body.includes(text), `every message must survive the batching — "${text}" is missing`);
  }
  // Order is part of the contract: "in order" is a lie if the bodies are shuffled.
  assert.ok(body.indexOf("one") < body.indexOf("two"));
  assert.ok(body.indexOf("two") < body.indexOf("three"));
  assert.ok(body.indexOf("three") < body.indexOf("four"));
  // …and each one is individually answered, or the queue never drains.
  assert.deepEqual(r.patches.map((p) => p.id).sort(), ["c1", "c2", "c3", "c4"]);
  assert.ok(r.patches.every((p) => p.status === "completed"), "all four must be marked completed");
});

test("A SINGLE STEER IS NOT WRAPPED — the envelope is overhead it has not earned", () => {
  // The other half. If one message arrived wrapped in batch scaffolding, every ordinary steer would cost the
  // agent a header explaining a batch of one.
  const r = apply({ controls: [steer("c1", "just this")] });
  assert.deepEqual(r.steers, ["just this"], "a lone steer must reach the controller verbatim");
  assert.deepEqual(r.patches, [{ id: "c1", status: "completed", response: "steer accepted" }]);
});

test("EVERY CONTROL IS ANSWERED even when applying it fails", () => {
  // A claimed-but-unanswered control is stuck forever, and nothing reports it. Failure must still PATCH.
  const r = apply({
    controls: [{ id: "c1", action: "interrupt" }],
    interruptThrows: "runtime exploded",
  });
  assert.equal(r.threw, null, "a failing control must not throw into the dispatch loop");
  assert.deepEqual(r.patches, [{ id: "c1", status: "failed", response: "runtime exploded" }]);
});

test("one bad control does not abandon the ones behind it", () => {
  // Each non-steer control has its own try/catch. Without that, an unknown action early in the list would
  // strand every control after it.
  const r = apply({
    controls: [
      { id: "c1", action: "nonsense" },
      { id: "c2", action: "interrupt" },
      { id: "c3", action: "nonsense-too" },
    ],
  });
  assert.equal(r.interrupts, 1, "the valid control between two bad ones must still be applied");
  const byId = Object.fromEntries(r.patches.map((p) => [p.id, p]));
  assert.equal(byId.c1.status, "failed");
  assert.match(byId.c1.response, /Unknown control action "nonsense"/);
  assert.equal(byId.c2.status, "completed");
  assert.equal(byId.c3.status, "failed", "…and the one AFTER the failure is still answered");
});

test("A FAILED STEER FAILS THE WHOLE BATCH, because none of them were applied", () => {
  // The batch is one call. If it throws, reporting some of its members as completed would tell the operator
  // messages were delivered that never reached the agent.
  const r = apply({
    controls: [steer("c1", "one"), steer("c2", "two"), steer("c3", "three")],
    steerThrows: "steer rejected",
  });
  assert.equal(r.patches.length, 3);
  assert.ok(r.patches.every((p) => p.status === "failed"), "no member of a failed batch may read completed");
  assert.ok(r.patches.every((p) => p.response === "steer rejected"));
});

test("an unsupported capability is REPORTED, not thrown", () => {
  // Runtimes differ. Throwing here would take the dispatch loop down for every agent this bridge serves, so
  // the control is answered with why instead.
  for (const [action, capabilities] of [
    ["interrupt", { steer: true, interrupt: false }],
    ["steer", { steer: false, interrupt: true }],
  ]) {
    const r = apply({ controls: [{ id: "c1", action, body: "x" }], capabilities });
    assert.equal(r.threw, null, `an unsupported ${action} must not throw`);
    assert.equal(r.patches.length, 1, "…and must still be answered");
    assert.equal(r.patches[0].status, "failed");
    assert.match(r.patches[0].response, new RegExp(`${action} is not supported by this runtime`, "i"));
  }
});

test("with no run or no controller it does not even claim", () => {
  // The common case on an idle poll. Claiming controls for a run that is not executing would take them off
  // the queue with nothing able to apply them.
  for (const activeRun of [{ runId: "" }, { runId: null }]) {
    const r = apply({ controls: [steer("c1", "x")], activeRun });
    assert.deepEqual(r.patches, [], "nothing may be claimed or answered");
    assert.deepEqual(r.steers, []);
  }
});

test("steers and non-steers in one claim are both handled", () => {
  // The mixed case, which is what the two-list split exists for: the interrupt goes through the per-control
  // path and the steers through the batch path, in the same call.
  const r = apply({
    controls: [steer("s1", "alpha"), { id: "i1", action: "interrupt" }, steer("s2", "beta")],
  });
  assert.equal(r.interrupts, 1);
  assert.equal(r.steers.length, 1, "the two steers batch together despite the interrupt between them");
  assert.match(r.steers[0], /2 messages arrived/);
  assert.deepEqual(r.patches.map((p) => p.id).sort(), ["i1", "s1", "s2"]);
  assert.ok(r.patches.every((p) => p.status === "completed"));
});

test("exactly one module declares it, and the bridge still calls it", () => {
  assert.deepEqual(declaringModules("processRunControls"),
    [{ file: "run-controls.mjs", kind: "function" }],
    "a second declaration would let two code paths answer the same control differently");
  // BRIDGE-WIDE, not server.js. The dispatch pass moved to `dispatch-loop.mjs` in v0.5.4 and this went
  // red on a pure relocation — the intent was always "the bridge still calls it", and naming the file
  // it happened to live in is what made that intent break on a move.
  assert.equal(isUsedInBridge("processRunControls"), true,
    "the dispatch pass must still apply controls somewhere in the bridge");
  // The no-re-declaration half is already covered, and better, by `declaringModules` above: it scans
  // the WHOLE bridge and requires exactly one declaration, where this only ever looked at server.js.
});

test("the owner holds no state and reaches only owned leaves", () => {
  const src = fs.readFileSync(path.join(STDIO, "run-controls.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, ["./aify-service-endpoint.mjs", "./runtimes.js"]);
});
