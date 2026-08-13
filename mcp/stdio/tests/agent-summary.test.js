// How an agent can be woken — fourteen ordered branches, tested for the first time.
//
// `wakeModeSummary` answers the question every dispatch decision rests on: given this agent's runtime,
// session mode, capabilities, handle and runtime config, what mechanism reaches it? Its answer is what an
// operator reads in `comms_agents` and `comms_agent_info`, and it is how they tell a merely idle agent
// from a structurally unreachable one.
//
// A WRONG ANSWER HERE IS NOT A CRASH. It is an operator being told an agent is reachable when it is not,
// or debugging a delivery lane that was never live. That failure survives a green suite indefinitely,
// which it did — until v0.5.4 this lived in `server.js`, the bin entry point, and nothing imported it.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { runtimeSummary, wakeModeSummary } from "../agent-summary.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const wsGateway = JSON.stringify({ gatewayUrl: "wss://gateway.example" });

test("an explicit wakeMode is trusted over anything derivable", () => {
  // The service can state it directly; when it does, none of the inference below should run. A derived
  // answer overriding a stated one would make the two disagree with no way to tell which is authoritative.
  assert.equal(wakeModeSummary({ wakeMode: "managed-worker", runtime: "claude-code" }), "managed-worker");
  assert.equal(wakeModeSummary({ wakeMode: "  spaced  ", runtime: "codex" }), "spaced", "trimmed");
  // But an empty or whitespace-only value is NOT a statement, and must fall through to inference.
  assert.equal(wakeModeSummary({ wakeMode: "   ", sessionMode: "managed", capabilities: ["managed-run"] }),
    "managed-worker", "blank is absent, not an answer");
});

test("a managed agent with managed-run is a managed worker, whatever its runtime", () => {
  for (const runtime of ["claude-code", "codex", "hermes", "pi", "generic"]) {
    assert.equal(
      wakeModeSummary({ sessionMode: "managed", runtime, capabilities: ["managed-run"] }),
      "managed-worker", `managed ${runtime} must be a managed worker`,
    );
  }
  // Without the capability it is not deliverable that way, and must not claim to be.
  assert.notEqual(wakeModeSummary({ sessionMode: "managed", runtime: "codex", capabilities: [] }), "managed-worker");
});

test("ORDER: codex-live is a STRICT SUPERSET of codex-thread-resume, and must be checked first", () => {
  // The most dangerous overlap in the function. Both branches require resident + codex + resident-run +
  // sessionHandle; codex-live additionally requires a live app-server in the runtime config. If the two
  // branches were swapped, EVERY live codex agent would report the fallback path — the resume mechanism
  // instead of the live one — and an operator would debug a lane that was not the one in use.
  const base = { sessionMode: "resident", runtime: "codex", capabilities: ["resident-run"], sessionHandle: "h1" };
  // `ws://`, not `http://`. I wrote this with an http URL first and it fell through to the resume path —
  // the code was right and my fixture was wrong. `hasCodexLiveAppServer` requires a WEBSOCKET scheme,
  // because a live app-server is one this bridge can hold a socket to. Read from the predicate rather than
  // assumed, after the assumed version failed.
  const live = { ...base, runtimeConfig: JSON.stringify({ appServerUrl: "ws://127.0.0.1:1455" }) };

  assert.equal(wakeModeSummary(base), "codex-thread-resume", "no app-server: resume path");
  const liveAnswer = wakeModeSummary(live);
  assert.notEqual(liveAnswer, "codex-thread-resume",
    "with a live app-server it must NOT report the fallback — that is the swapped-branch symptom");
  assert.equal(liveAnswer, "codex-live");

  // And an HTTP app-server URL is deliberately NOT live. Kept as its own assertion because it is the
  // distinction my first fixture got wrong, and a future relaxation of the scheme check would report every
  // http-configured codex agent as holding a live socket it does not have.
  assert.equal(
    wakeModeSummary({ ...base, runtimeConfig: JSON.stringify({ appServerUrl: "http://127.0.0.1:1455" }) }),
    "codex-thread-resume", "an http app-server is not a live one",
  );
});

test("ORDER: hermes keys on the GATEWAY, never on the handle", () => {
  // Recorded in the source as a correction: the handle is now the agent's real hermes session id, so a
  // handle proves nothing about deliverability. Only a live ws:// gateway does. A version keyed on the
  // handle would report a hermes agent reachable whenever it had ever registered a session.
  const withGateway = {
    sessionMode: "resident", runtime: "hermes", capabilities: ["resident-run"], runtimeConfig: wsGateway,
  };
  assert.equal(wakeModeSummary(withGateway), "hermes-live");
  assert.equal(wakeModeSummary({ ...withGateway, sessionHandle: "" }), "hermes-live",
    "no handle but a live gateway is still live");
  assert.equal(
    wakeModeSummary({ ...withGateway, runtimeConfig: JSON.stringify({ gatewayUrl: "http://not-ws" }) }),
    "hermes-missing-handle", "a non-ws gateway is not a live gateway");
  assert.equal(
    wakeModeSummary({ ...withGateway, runtimeConfig: "", sessionHandle: "h1" }),
    "hermes-missing-handle", "a handle does NOT substitute for a gateway");
});

test("the missing-prerequisite answers name the runtime, so a silent agent is diagnosable", () => {
  // These exist to distinguish "idle" from "cannot be reached at all". A generic "message-only" for every
  // broken case would tell an operator nothing about what to fix.
  const r = (runtime, extra = {}) =>
    wakeModeSummary({ sessionMode: "resident", runtime, capabilities: ["resident-run"], ...extra });
  assert.equal(r("codex"), "codex-missing-handle");
  assert.equal(r("opencode"), "opencode-missing-handle");
  assert.equal(r("pi"), "pi-missing-handle");
  assert.equal(r("hermes"), "hermes-missing-handle");
});

test("each resident runtime with a handle gets its own resume mechanism", () => {
  const r = (runtime) => wakeModeSummary({
    sessionMode: "resident", runtime, capabilities: ["resident-run"], sessionHandle: "h1",
  });
  assert.equal(r("opencode"), "opencode-session-resume");
  assert.equal(r("pi"), "pi-session-resume");
  assert.equal(r("claude-code"), "claude-live", "claude with resident-run is live, no handle needed");
});

test("a resident claude WITHOUT resident-run needs a channel, and says so", () => {
  assert.equal(wakeModeSummary({ sessionMode: "resident", runtime: "claude-code", capabilities: [] }),
    "claude-needs-channel");
});

test("anything else falls back to message-only, and never throws", () => {
  // The floor. An unknown runtime, a missing capabilities array, a malformed runtimeConfig — none may
  // produce an exception, because this function runs while RENDERING a status report.
  for (const info of [
    {}, { sessionMode: "resident" }, { runtime: "brand-new-runtime", sessionMode: "resident" },
    { sessionMode: "resident", runtime: "codex", capabilities: "not-an-array", sessionHandle: "h" },
    { sessionMode: "resident", runtime: "hermes", capabilities: ["resident-run"], runtimeConfig: "{broken" },
    { sessionMode: "managed", capabilities: null },
  ]) {
    let out;
    assert.doesNotThrow(() => { out = wakeModeSummary(info); }, `threw on ${JSON.stringify(info)}`);
    assert.equal(typeof out, "string");
    assert.ok(!/undefined|NaN|\[object Object\]/.test(out), `leaked a placeholder: ${out}`);
  }
  assert.equal(wakeModeSummary({}), "message-only");
});

test("runtimeSummary names the runtime, the machine and the mode, and never leaks a placeholder", () => {
  assert.match(runtimeSummary({ runtime: "codex", machineId: "box-1", sessionMode: "managed" }),
    /codex @ box-1 \(managed\)/);
  // snake_case arrives from the service alongside camelCase; both must be read.
  assert.match(runtimeSummary({ runtime: "pi", machine_id: "box-2", session_mode: "managed" }),
    /pi @ box-2 \(managed\)/);
  // With no machine given it falls back to THIS machine, which is the honest answer for a local agent.
  const local = runtimeSummary({ runtime: "codex" });
  assert.ok(!/undefined|null/.test(local), `leaked a placeholder: ${local}`);
  assert.match(local, /\(resident\)/, "an unstated mode is resident, matching normalizeSessionMode");
  assert.ok(!/undefined/.test(runtimeSummary({})), "an empty record must still render");
});

test("server.js declares neither — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["runtimeSummary", "wakeModeSummary"]) {
    assert.doesNotMatch(src, new RegExp(`^(?:export\\s+)?function\\s+${name}\\b`, "m"), `${name} must be imported`);
    assert.match(src, new RegExp(`(?<![\\w.])${name}\\(`), `server.js is still expected to CALL ${name}`);
  }
});

test("the leaf reaches only owned leaves, and holds no mutable state", () => {
  const src = readFileSync(path.join(STDIO, "agent-summary.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, ["./parse-json.mjs", "./runtimes.js", "./session-mode.mjs"]);
});
