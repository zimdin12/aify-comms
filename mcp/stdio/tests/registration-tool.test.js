// `comms_register` — and specifically the one dependency it is allowed to ask for.
//
// The dispatch loop is INJECTED into this tool rather than imported, because importing it would drag
// `runDispatchLoop` and 34 other functions into a registration module. That shape is only defensible if the
// module genuinely asks for the loop rather than owning it, so these tests assert both halves: that
// registration DOES start the loop when a remote registration succeeds, and that it does NOT when there is
// nothing to poll for or nothing succeeded. Asserting only that the parameter is present would prove the
// wiring compiles, not that the branch is right — and the branch is what a stranded queued turn depends on.
//
// EVERY TEST RUNS IN A CHILD, and the fake service binds 127.0.0.2. Both are safety requirements. `IS_REMOTE`
// is resolved once at module load from the environment, so remote and local cannot be exercised in one
// process; and `defaultFallbackServerUrls` adds the real `127.0.0.1:8800` as a fallback for any loopback
// primary, which is how an earlier test in this repo posted to the operator's live service. Local mode is
// pointed at a temp `CLAUDE_MCP_MESSAGES_DIR` for the same reason.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules } from "./bridge-sources.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "registration-tool.mjs")).href;

// Register the real tool against a fake MCP server, invoke the real handler, and report what happened —
// including whether the injected `ensureDispatchLoop` was called.
//
// `status` is what the fake service answers `POST /api/v1/agents` with. The `/api/v1` prefix is not a
// guess: a first version of this fixture matched `/agents` exactly, the POST fell through to the catch-all,
// and the handler cheerfully reported `Registered "undefined"`. The probe is why the prefix is here.
function register({ remote = true, status = 200, preArm = false, args = {}, env = {} } = {}) {
  const store = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reg-test-"));
  // `{ agents: {} }`, not `{}`. `readAgents` guards a parse failure but not the SHAPE, so a valid-JSON file
  // of the wrong shape crashes the caller — which is what an empty-object fixture did here first.
  fs.writeFileSync(path.join(store, "agents.json"), '{"agents":{}}');
  const script = `
    import http from "node:http";
    const requests = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        requests.push({ method: req.method, url: req.url });
        if (req.method === "POST" && req.url.endsWith("/agents")) {
          res.writeHead(${status}, { "content-type": "application/json" });
          return res.end(JSON.stringify({
            agentId: "reg-test-agent", role: "coder", runtime: "generic",
            sessionMode: "resident", machineId: "test-box", capabilities: {},
          }));
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = ${remote} ? "http://127.0.0.2:" + srv.address().port : "";
    process.env.CLAUDE_MCP_SERVER_URL = "";

    const detector = await import(${JSON.stringify(pathToFileURL(path.join(STDIO, "claude-turn-detector-state.mjs")).href)});
    if (${preArm}) detector.armClaudeTurnEndDetector("reg-test-agent");
    const armedBefore = detector.isClaudeTurnDetectorArmed();

    const { registerRegistrationTool } = await import(${JSON.stringify(LEAF)});
    const { z } = await import("zod");

    let loopCalls = 0;
    const tools = new Map();
    registerRegistrationTool(
      { tool: (name, description, schema, handler) => tools.set(name, { description, schema, handler }) },
      z,
      { ensureDispatchLoop: () => { loopCalls += 1; } },
    );

    let text = null; let error = null;
    try {
      const out = await tools.get("comms_register").handler(${JSON.stringify({
        agentId: "reg-test-agent", role: "coder", runtime: "generic", ...args,
      })});
      text = out?.content?.[0]?.text || null;
    } catch (e) { error = String(e?.message || e); }
    srv.close();
    process.stdout.write(JSON.stringify({
      tools: [...tools.keys()], loopCalls, text, error, requests,
      armedBefore, armedAfter: detector.isClaudeTurnDetectorArmed(),
    }));
  `;
  try {
    const out = execFileSync(process.execPath, ["--input-type=module", "-e", script], {
      env: {
        ...sealedChildEnv(),
        AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "",
        CLAUDE_MCP_MESSAGES_DIR: store,
        AIFY_AGENT_ID: "reg-test-agent",
        // EXPLICIT, because it decides whether the detector can arm at all. `__runtimeAdapter` is resolved
        // from `AIFY_RUNTIME` at module load, and `armClaudeTurnEndDetector` refuses unless the adapter is
        // claude-code. The first version of these tests inherited the ambient value — they passed only
        // because this session happens to BE claude-code, and would have failed anywhere else.
        AIFY_RUNTIME: "",
        // EVERY OTHER AMBIENT INPUT THIS HANDLER READS, pinned for the same reason and after the same
        // mistake. A reviewer running the suite on a live managed hermes agent inherits
        // `AIFY_MANAGED_DISPATCH=1`, which sends every registration below into the managed-dispatch guard
        // and fails four tests that have nothing to do with it; and an ambient gateway URL or an
        // agent-keyed gateway marker under `TEMP` reaches `hermes-gateway-config.mjs` at load. The rule
        // this file now follows: if the code reads it, the fixture sets it.
        AIFY_MANAGED_DISPATCH: "",
        AIFY_HERMES_GATEWAY_URL: "", AIFY_HERMES_GATEWAY_TOKEN_ENV: "",
        TEMP: store, TMP: store, XDG_STATE_HOME: path.join(store, "state"),
        ...env,
      },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
    });
    return JSON.parse(out);
  } finally {
    fs.rmSync(store, { recursive: true, force: true });
  }
}

test("the tool registers under its own name, with a schema", () => {
  const r = register();
  assert.deepEqual(r.tools, ["comms_register"], "the module registers exactly the one tool it is named for");
});

test("A SUCCESSFUL REMOTE REGISTRATION STARTS THE DISPATCH LOOP", () => {
  // The invocation half. A local agent that registers and never starts the loop sits with queued work and
  // no poller — the work is not lost, it simply never runs, which is the worst way for this to fail because
  // nothing errors.
  const r = register({ remote: true, status: 200 });
  assert.equal(r.error, null, `registration should succeed: ${r.error}`);
  assert.equal(r.loopCalls, 1, "a successful remote registration must ask for the dispatch loop, exactly once");
  assert.match(r.text, /Registered "reg-test-agent"/, "…and must really have registered, not short-circuited");
});

test("A FAILED REMOTE REGISTRATION DOES NOT START IT", () => {
  // The non-invocation half, and the more interesting one. Starting a poll loop for an agent the service
  // never accepted would have this bridge claiming work on behalf of an agent that does not exist.
  const r = register({ remote: true, status: 500 });
  assert.ok(r.error, "a 500 must surface as an error rather than a successful-looking registration");
  assert.equal(r.loopCalls, 0, "nothing may start the dispatch loop when the registration failed");
});

test("LOCAL MODE REGISTERS WITHOUT STARTING IT, and without touching the network at all", () => {
  // The other non-invocation branch. In local mode there is no service to poll, so the loop would spin
  // against nothing. This also pins that local registration is genuinely offline: a single HTTP request
  // here would mean the local path had a remote dependency.
  const r = register({ remote: false });
  assert.equal(r.error, null, `local registration should succeed: ${r.error}`);
  assert.match(r.text, /Registered "reg-test-agent"/);
  assert.equal(r.loopCalls, 0, "local mode has no service to poll and must not start the loop");
  assert.deepEqual(r.requests, [], "local registration must make no HTTP requests");
});

test("REGISTERING ARMS THE TURN DETECTOR LATE when the wrapper never exported an identity", () => {
  // 2026-07-14. A session launched without `--aify-agent` has no `AIFY_AGENT_ID` at boot, so the boot-time
  // arm no-ops and the bridge has NO way to report turn state — the agent registers, messages and heartbeats
  // perfectly while its status latches forever. `comms_register` is the moment the bridge learns who it is,
  // and therefore the last chance to arm.
  const r = register({ remote: true, env: { AIFY_RUNTIME: "claude-code" } });
  assert.equal(r.armedBefore, false, "the fixture must start unarmed, or this proves nothing");
  assert.equal(r.armedAfter, true, "registering must arm the turn-end detector");
});

test("…but NOT for a runtime that has no claude transcript to watch", () => {
  // The non-invocation half. The detector reads a claude transcript tail; arming it for codex would start a
  // watcher over a transcript that does not exist. `armClaudeTurnEndDetector` enforces this itself, and this
  // asserts registration does not somehow route around it.
  for (const runtime of ["codex", "hermes", ""]) {
    const r = register({ remote: true, env: { AIFY_RUNTIME: runtime } });
    assert.equal(r.armedAfter, false, `a ${runtime || "(none)"} bridge must not arm the claude detector`);
    assert.equal(r.error, null, "…and must still register successfully");
  }
});

test("arming is idempotent — a live detector is never replaced", () => {
  // Registration guards with `!isClaudeTurnDetectorArmed()`, and `armClaudeTurnEndDetector` ALSO returns
  // early when already armed. The double guard is stated rather than glossed: this test pins the OUTCOME
  // (still armed, still working), not the register-side check, because removing that check alone is not
  // observable — the owner's own guard still holds. A test claiming otherwise would be vacuous.
  const r = register({ remote: true, preArm: true, env: { AIFY_RUNTIME: "claude-code" } });
  assert.equal(r.armedBefore, true, "the fixture pre-armed it");
  assert.equal(r.armedAfter, true, "…and it stays armed");
  assert.equal(r.error, null, "re-registration over an armed detector must still succeed");
});

test("THE MODULE ASKS FOR THE LOOP AND DOES NOT OWN IT", () => {
  // The structural claim that makes the injection defensible rather than a trick to shrink a number. If any
  // of these appeared here, registration would have taken ownership of process lifecycle.
  const src = fs.readFileSync(path.join(STDIO, "registration-tool.mjs"), "utf-8");
  for (const forbidden of [
    /^(export\s+)?(async\s+)?function\s+runDispatchLoop\b/m,
    /^(export\s+)?(async\s+)?function\s+ensureDispatchLoop\b/m,
    /(?<![\w.])dispatchLoopTimer(?![\w])/,
    /(?<![\w.])DISPATCH_POLL_MS(?![\w])/,
    /setInterval\s*\(/,
  ]) {
    assert.doesNotMatch(src, forbidden, `registration must not own the dispatch loop: ${forbidden}`);
  }
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  // It is a FUNCTION, not a module-scope registration side effect — the same shape every other extracted
  // tool group uses, so importing it cannot register a tool as a side effect of loading.
  assert.match(src, /^export function registerRegistrationTool\(server, z, \{ ensureDispatchLoop \}\)/m);
  assert.doesNotMatch(src, /^server\.tool\(/m, "nothing may register at module scope");
});

test("server.js owns the loop, passes it in, and no longer registers the tool itself", () => {
  assert.deepEqual(
    declaringModules("registerRegistrationTool"),
    [{ file: "registration-tool.mjs", kind: "function" }],
    "exactly one module declares the wrapper",
  );
  const server = fs.readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.match(server, /^function ensureDispatchLoop\(/m,
    "server.js keeps the implementation — this is the borrow, not a move");
  // The CALL moved to `register-tools.mjs` with the rest of the registration list; the
  // IMPLEMENTATION stays in server.js, which the assertion above pins. Together they still prove
  // the borrow: server.js owns `ensureDispatchLoop` and hands it down, rather than the tool
  // importing it for itself.
  const reg = fs.readFileSync(path.join(STDIO, "register-tools.mjs"), "utf-8");
  assert.match(reg, /registerRegistrationTool\(server, z, \{ ensureDispatchLoop \}\);/,
    "…and it is handed the borrow rather than importing it");
  assert.doesNotMatch(server, /"comms_register"/,
    "server.js must not still declare the tool it delegated");
});

test("the module reaches only owned leaves", () => {
  const src = fs.readFileSync(path.join(STDIO, "registration-tool.mjs"), "utf-8");
  const imports = [...src.matchAll(/^} from "([^"]+)";$|^import .* from "([^"]+)";$/gm)]
    .map((m) => m[1] || m[2]).sort();
  assert.deepEqual(imports, [
    "./aify-service-endpoint.mjs",
    "./binding-file.js",
    "./bridge-agent-state.mjs",
    "./bridge-instance.mjs",
    "./claude-turn-detector-state.mjs",
    // REVIEWED, 2026-08-29. A PURE leaf that imports nothing: it answers whether this process may
    // claim environment ownership of the agent it is registering. It is here because the answer used
    // to be "always yes", and a managed agent's own sidecar was overwriting the environment bridge's
    // id in `runtimeState.bridgeInstanceId` -- observed live, two different ids for one agent within
    // minutes, and `aify-comms doctor` calling an answering agent an orphan on the strength of it.
    "./environment-ownership-claim.mjs",
    "./hermes-endpoint.js",
    "./launch-identity.mjs",
    "./local-active-run.mjs",
    "./local-store.mjs",
    "./register-helpers.js",
    "./register-identity.js",
    "./registration-inputs.mjs",
    "./runtime-adapter.mjs",
    "./runtimes.js",
    "./safe-name.mjs",
    "./session-mode.mjs",
    "fs",
    "path",
  ], "a new import here is a new dependency for the registration path — review it");
  assert.ok(!imports.includes("./server.js"), "and never the file it was extracted from");
});
