#!/usr/bin/env node
import assert from "node:assert/strict";
// Imported from its OWNER. It used to come from `server.js`, the bin entry point, so a test of a
// pure decision function loaded the entire bridge.
import { reportResidentLost } from "../resident-lost.mjs";

// reportResidentLost is the seam shutdownWithStatus calls on the clean-exit
// path: when an operator cleanly closes a RESIDENT *-aify session, the bridge
// self-corrects off `available` by POSTing /agents/{id}/resident-lost (with its
// owning bridgeId) instead of waiting out the ~150s heartbeat lease. Managed
// sessions must NEVER POST resident-lost (managed teardown is terminal reaping).

// ---------------------------------------------------------------------------
// 1. RESIDENT clean exit DOES POST /resident-lost, carrying the bridge id so
//    the server's bridge_not_current guard accepts it as the owning bridge.
// ---------------------------------------------------------------------------
{
  const calls = [];
  const httpCall = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { transition: "resident->managed" };
  };

  const posted = await reportResidentLost({
    httpCall,
    agentId: "operator-resident",
    bridgeId: "bridge-abc",
    sessionMode: "resident",
    runtime: "claude-code",
  });

  assert.equal(posted, true, "resident clean exit reports a POST was attempted");
  assert.equal(calls.length, 1, "exactly one POST on the resident path");
  assert.equal(calls[0].method, "POST");
  assert.equal(
    calls[0].endpoint,
    "/agents/operator-resident/resident-lost",
    "POSTs to the resident-lost endpoint for this agent",
  );
  assert.equal(
    calls[0].body.bridgeId,
    "bridge-abc",
    "carries the owning bridge id (passes server bridge_not_current guard)",
  );
}

// ---------------------------------------------------------------------------
// 2. MANAGED clean exit does NOT POST /resident-lost. A managed bridge must
//    never flip its own agent off `available`; teardown is terminal reaping.
// ---------------------------------------------------------------------------
{
  const calls = [];
  const httpCall = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return {};
  };

  const posted = await reportResidentLost({
    httpCall,
    agentId: "sc-coder",
    bridgeId: "bridge-xyz",
    sessionMode: "managed",
    runtime: "claude-code",
  });

  assert.equal(posted, false, "managed path reports NO POST");
  assert.equal(calls.length, 0, "managed path never POSTs resident-lost");
}

// ---------------------------------------------------------------------------
// 3. No agent id bound → no POST (the outer gate). Default/empty session mode
//    is treated as resident (matching the bridge's resident-by-default), but
//    without an agent id there is nothing to report.
// ---------------------------------------------------------------------------
{
  const calls = [];
  const httpCall = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return {};
  };

  const posted = await reportResidentLost({
    httpCall,
    agentId: "",
    bridgeId: "bridge-none",
    sessionMode: "resident",
  });

  assert.equal(posted, false, "no agent id → no POST");
  assert.equal(calls.length, 0, "no agent id → never POSTs");
}

// ---------------------------------------------------------------------------
// 4. A short-lived MCP child does not own the resident lifecycle. Hermes' visible
//    TUI is owned by its wrapper + managed-host loop, so an MCP child exit must
//    not mark the still-open operator TUI stopped.
// ---------------------------------------------------------------------------
{
  const calls = [];
  const posted = await reportResidentLost({
    httpCall: async (...args) => { calls.push(args); },
    agentId: "operator-hermes",
    bridgeId: "bridge-per-turn-child",
    sessionMode: "resident",
    lifecycleOwner: "managed-host",
    runtime: "hermes",
  });

  assert.equal(posted, false, "non-owning MCP child exit reports NO POST");
  assert.equal(calls.length, 0, "non-owning MCP child never marks the resident stopped");
}

// ---------------------------------------------------------------------------
// 5. Best-effort: a throwing httpCall is swallowed (never throws out of the
//    exit path) and reports false.
// ---------------------------------------------------------------------------
{
  const httpCall = async () => { throw new Error("server unreachable"); };
  let threw = false;
  let posted = true;
  try {
    posted = await reportResidentLost({
      httpCall,
      agentId: "operator-resident",
      bridgeId: "bridge-abc",
      sessionMode: "resident",
    });
  } catch {
    threw = true;
  }
  assert.equal(threw, false, "resident-lost POST failure is swallowed (best-effort)");
  assert.equal(posted, false, "swallowed failure reports false");
}

console.log("resident-clean-exit-lost: all assertions passed");

// ── ownership, added when this moved out of the bin entry point in v0.5.4 ──────────────────────────
import fsOwn from "node:fs";
import pathOwn from "node:path";
import { fileURLToPath as f2u } from "node:url";
import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

{
  const stdio = pathOwn.resolve(pathOwn.dirname(f2u(import.meta.url)), "..");
  assert.deepEqual(declaringModules("reportResidentLost"),
    [{ file: "resident-lost.mjs", kind: "function" }],
    "a second copy could report a loss the gates were meant to refuse");
  assert.ok(isUsedInBridge("reportResidentLost"), "the clean-exit path must still call it");
  const src = fsOwn.readFileSync(pathOwn.join(stdio, "resident-lost.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  // Scoped to an IMPORT, not to the word: the module's own header explains that `httpCall` is a parameter,
  // and a bare word-match forbids the explanation as well as the thing.
  assert.doesNotMatch(src, /^import \{[^}]*\bhttpCall\b/m,
    "the transport stays INJECTED — importing one would remove the seam this is testable through");
  assert.match(src, /httpCall: call/, "…and it is still destructured from the caller's argument");
  assert.deepEqual([...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort(),
    ["./runtimes.js", "./session-mode.mjs"]);
}
