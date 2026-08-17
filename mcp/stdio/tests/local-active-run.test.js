// Whether this bridge still believes a run is active locally, checked against what the service says.
//
// THE ASYMMETRY IS THE WHOLE DESIGN, and it is what these tests are for. Only a 404 — the service
// affirmatively saying the run is gone — may drop the local record. Any other error keeps it. Forgetting a
// run that IS executing frees the claim loop to take the same work again, so the agent runs it twice; a
// stale record only costs one blocked claim cycle. Those two failures are not symmetric, and a reconciler
// that treated a timeout like a 404 would trade the cheap one for the expensive one.
//
// The fake service runs INSIDE the child and binds 127.0.0.2. Both are safety requirements: `execFileSync`
// blocks the parent's event loop so a parent-hosted server cannot answer, and `defaultFallbackServerUrls`
// adds the real `127.0.0.1:8800` as a fallback for any loopback primary — which is how an earlier version of
// a test in this repo posted to the operator's live service.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "local-active-run.mjs")).href;
const STATE = pathToFileURL(path.join(STDIO, "bridge-agent-state.mjs")).href;

// Run a reconcile against a fake service that answers `/dispatch/runs/:id` with the given status/body, and
// report what happened: the return value, whether the run is still in ACTIVE_RUNS, and every request made.
function reconcileWith({ status = 200, run = null, active = { runId: "run-1", runtime: "codex" } } = {}) {
  const script = `
    import http from "node:http";
    const requests = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        requests.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : null });
        if (req.url.includes("/dispatch/runs/")) {
          res.writeHead(${status}, { "content-type": "application/json" });
          return res.end(JSON.stringify(${JSON.stringify({ run: null })}).replace('"run":null', '"run":' + ${JSON.stringify(JSON.stringify(run))}));
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;
    process.env.CLAUDE_MCP_SERVER_URL = "";
    const state = await import(${JSON.stringify(STATE)});
    const m = await import(${JSON.stringify(LEAF)});
    let interrupted = false;
    const active = { ...${JSON.stringify(active)}, controller: { interrupt: () => { interrupted = true; } } };
    state.ACTIVE_RUNS.set("agent-a", active);
    const dropped = await m.reconcileLocalActiveRun("agent-a", { info: { runtime: "codex" } }, active);
    srv.close();
    process.stdout.write(JSON.stringify({
      dropped,
      stillTracked: state.ACTIVE_RUNS.has("agent-a"),
      interrupted,
      requests,
    }));
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script],
    { env: { ...sealedChildEnv(), AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "" },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] }));
}

test("a 404 — the service says the run is gone — DROPS the local record", () => {
  // The only affirmative signal. The service has no such run, so continuing to hold it blocks the claim
  // loop for nothing.
  const r = reconcileWith({ status: 404 });
  assert.equal(r.dropped, true, "a 404 must drop the stale local run");
  assert.equal(r.stillTracked, false, "…and remove it from ACTIVE_RUNS");
  assert.equal(r.interrupted, true, "…and interrupt whatever controller was holding it");
});

test("A NON-404 ERROR KEEPS THE RUN — the asymmetry this module exists for", () => {
  // A 500 or a timeout means the service could not answer, NOT that the run is gone. Dropping here would
  // free the claim loop to take work the agent is still executing, and it would run twice.
  for (const status of [500, 502, 503]) {
    const r = reconcileWith({ status });
    assert.equal(r.dropped, false, `HTTP ${status} must NOT drop the run`);
    assert.equal(r.stillTracked, true, `…and must leave it in ACTIVE_RUNS`);
    assert.equal(r.interrupted, false, `…and must not interrupt a run that may be executing`);
  }
});

test("a backend run that is still live and still OURS keeps the local record", () => {
  // 200 with a matching, non-terminal run and no conflicting owner. Read from `shouldDropLocalActiveRun`
  // after my first fixture set `bridgeId: "someone"` and the run was dropped — correctly, because that says
  // another bridge owns it. The predicate keeps a run only when id, status, target agent and bridge all
  // agree or are absent.
  const r = reconcileWith({ status: 200, run: { id: "run-1", status: "running", targetAgentId: "agent-a" } });
  assert.equal(r.dropped, false, "a run the service still reports as ours must not be dropped");
  assert.equal(r.stillTracked, true);
  assert.equal(r.interrupted, false, "…and nothing may interrupt a live run");
});

test("a run the service says belongs to ANOTHER BRIDGE is dropped — the supersede case", () => {
  // What my wrong fixture was actually exercising, kept because the property is load-bearing. After a
  // supersede the service reassigns the run to the new bridge; the old one must stop believing it owns it,
  // or two bridges drive the same run.
  const r = reconcileWith({ status: 200, run: { id: "run-1", status: "running", bridgeId: "a-different-bridge" } });
  assert.equal(r.dropped, true, "a run owned by another bridge must be dropped locally");
  assert.equal(r.stillTracked, false);
});

test("a run the service reports for a DIFFERENT agent is dropped", () => {
  // Same family: the local record is stale in a way that would have this bridge report turn state for work
  // belonging to someone else.
  const r = reconcileWith({ status: 200, run: { id: "run-1", status: "running", targetAgentId: "someone-else" } });
  assert.equal(r.dropped, true);
});

test("a run the service reports as FINISHED is dropped", () => {
  // The ordinary end-of-life path: the service knows the run completed and this bridge has not noticed.
  for (const status of ["completed", "failed", "cancelled"]) {
    const r = reconcileWith({ status: 200, run: { id: "run-1", status, targetAgentId: "agent-a" } });
    assert.equal(r.dropped, true, `a ${status} run must be dropped locally`);
  }
});

test("with no active run it does nothing and reports nothing dropped", () => {
  // The common case — most reconcile calls have nothing to reconcile. It must not reach the service at all.
  const r = reconcileWith({ active: { runId: "" } });
  assert.equal(r.dropped, false);
  assert.equal(r.requests.filter((q) => q.url.includes("/dispatch/runs/")).length, 0,
    "an absent run must not cost a service round-trip");
});

test("dropping a run REPORTS THE AGENT NO LONGER BUSY", () => {
  // The third thing clearing does, and the easiest to lose. Without it the agent reads `working` with no run
  // able to clear it — exactly the stuck-at-turn_busy symptom the heartbeat exists to prevent.
  const r = reconcileWith({ status: 404 });
  const beat = r.requests.find((q) => q.url.includes("/heartbeat"));
  assert.ok(beat, "clearing must post a heartbeat");
  assert.equal(beat.body.turnBusy, false, "…and it must report NOT busy");
  assert.equal(beat.body.turnRunId, "run-1", "…naming the run being cleared");
});

test("exactly one module declares each, and server.js still calls them", () => {
  for (const name of ["clearLocalActiveRun", "reconcileLocalActiveRun"]) {
    assert.deepEqual(
      declaringModules(name), [{ file: "local-active-run.mjs", kind: "function" }],
      `${name} must be declared exactly once, by its owner`,
    );
  }
  const server = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  // BRIDGE-WIDE: the caller moved to `dispatch-loop.mjs` in v0.5.4 with the dispatch pass.
  assert.equal(isUsedInBridge("reconcileLocalActiveRun"), true,
    "the bridge must still reconcile a local active run");
});

test("the owner holds no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO, "local-active-run.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    "./agent-heartbeat.mjs",
    "./aify-service-endpoint.mjs",
    "./bridge-agent-state.mjs",
    "./bridge-instance.mjs",
    "./dispatch-state.js",
    "./runtimes.js",
  ]);
  // It mutates the OWNED ACTIVE_RUNS rather than a private copy — a second map would make the bridge and
  // this reconciler disagree about which runs are live.
  assert.match(src, /ACTIVE_RUNS\.delete\(agentId\)/, "it must clear from the owned map");
  assert.doesNotMatch(src, /^(?:export\s+)?const ACTIVE_RUNS/m, "and must not declare its own");
});
