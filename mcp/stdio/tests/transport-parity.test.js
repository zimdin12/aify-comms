#!/usr/bin/env node
// The two transports must not drift apart by ACCIDENT.
//
// AUDIT FINDING 2. Twenty tools are implemented in BOTH `mcp/stdio/server.js` and
// `mcp/sse_server.py`. That duplication is deliberate — different languages, and SSE is
// intentionally a reduced surface — but it has already cost us twice:
//
//   * `comms_search` silently searched artifacts only when `agentId` was omitted. I fixed the
//     stdio renderer, believed I was done, and found the SSE copy afterwards while bughunting my
//     own fix. Both had to declare `searched`/`skipped` before the absence bug was actually closed.
//   * `[MSG NEW]` was rendered from read state the API never returns, in BOTH transports.
//
// The reviewer's recommendation, which matches what has worked twice in this repo: do NOT
// consolidate — add agreement tests. Consolidating two languages is expensive and would fight the
// intentional reduction. A test that fails when they drift is cheap and catches the actual failure
// mode.
//
// This is the inventory gate: it does not demand parity, it demands that every difference be
// DECLARED. A tool that quietly exists in one transport and not the other is the accident; a tool
// listed below as intentionally-absent is a decision.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const STDIO = readFileSync(new URL("../server.js", import.meta.url), "utf8");
const SSE = readFileSync(new URL("../../sse_server.py", import.meta.url), "utf8");

// `server.tool("name",` — the stdio registration form.
function stdioTools() {
  return new Set([...STDIO.matchAll(/server\.tool\(\s*\n?\s*"(comms_[a-z_]+)"/g)].map((m) => m[1]));
}

// `@mcp_server.tool()` followed by `async def name(`.
function sseTools() {
  return new Set(
    [...SSE.matchAll(/@mcp_server\.tool\(\)\s*\nasync def\s+(comms_[a-z_]+)\s*\(/g)].map((m) => m[1]),
  );
}

// Tools deliberately absent from SSE, each with the reason. SSE is a REDUCED surface by design —
// it says so itself — so absence here is a decision, not a gap. Adding a name to this list is the
// declaration; leaving it out is what the test catches.
const INTENTIONALLY_STDIO_ONLY = {
  // Lifecycle verbs. SSE's own docstring says its clients cannot host local runtime launches.
  comms_restart: "lifecycle verb — SSE clients cannot host local runtime launches",
  comms_spawn: "lifecycle verb — same reason",
  comms_compact: "lifecycle verb — same reason",
  comms_delete_session: "session lifecycle management",
  comms_remove_agent: "destructive lifecycle verb",
  comms_interrupt: "runtime-native interrupt needs a local process",
  // Host-local reads the SSE server cannot perform.
  comms_usage: "reads host-side quota stores the SSE process cannot see",
  comms_envs: "environment/bridge inventory of the local host",
  comms_listen: "stdio transport primitive",
  // Not yet mirrored. These are the ones worth revisiting — comms_agent_info especially, since
  // audit finding 1 made it the place production is reported and an SSE caller cannot see it.
  comms_agent_info: "health surface, not yet mirrored — REVISIT: carries outbound activity since v0.3.1",
  comms_contracts: "reply-contract audit surface, not yet mirrored",
  comms_status: "status/focus write, not yet mirrored",
  comms_describe: "registration-adjacent, not yet mirrored",
  comms_unsend: "inbox management, not yet mirrored",
};

test("every SSE tool also exists in stdio", () => {
  // The reverse direction is the accident that matters most: a tool reachable over SSE but absent
  // from stdio would be unreviewed surface, since stdio is where the tool descriptions live.
  const missing = [...sseTools()].filter((t) => !stdioTools().has(t));
  assert.deepEqual(missing, [], `SSE exposes tools stdio does not: ${missing.join(", ")}`);
});

test("every stdio tool is either in SSE or DECLARED as intentionally stdio-only", () => {
  const sse = sseTools();
  const undeclared = [...stdioTools()].filter(
    (t) => !sse.has(t) && !Object.hasOwn(INTENTIONALLY_STDIO_ONLY, t),
  );
  assert.deepEqual(
    undeclared,
    [],
    "These stdio tools are missing from SSE and not declared as intentional:\n  "
      + undeclared.join("\n  ")
      + "\n\nEither implement them in mcp/sse_server.py, or add them to INTENTIONALLY_STDIO_ONLY "
      + "with the reason. Silent absence is how comms_search stayed half-fixed.",
  );
});

test("the intentional list does not name tools that DO exist in SSE", () => {
  // A stale exemption is worse than none: it declares a difference that is not there, so a real
  // future divergence in that tool would be silently permitted.
  const sse = sseTools();
  const stale = Object.keys(INTENTIONALLY_STDIO_ONLY).filter((t) => sse.has(t));
  assert.deepEqual(stale, [], `declared stdio-only but present in SSE: ${stale.join(", ")}`);
});

test("both transports actually parsed — a zero inventory would pass everything", () => {
  // The failure mode of every source-scanning test: the regex stops matching after a refactor and
  // the suite goes quietly green over an empty set.
  assert.ok(stdioTools().size >= 25, `stdio inventory looks wrong: ${stdioTools().size}`);
  assert.ok(sseTools().size >= 15, `SSE inventory looks wrong: ${sseTools().size}`);
});

test("the tools that turn API JSON into CONCLUSIONS exist in both", () => {
  // Ranked by the reviewer as the wrong-belief surface: these are where a caller decides something
  // is absent, delivered, or healthy. comms_search is the proven case.
  const both = stdioTools();
  const sse = sseTools();
  for (const t of ["comms_search", "comms_agents", "comms_send", "comms_run_status", "comms_inbox"]) {
    assert.ok(both.has(t), `${t} missing from stdio`);
    assert.ok(sse.has(t), `${t} missing from SSE — a conclusion-forming tool must not be one-sided`);
  }
});

test("comms_search declares its scope in BOTH transports", () => {
  // The concrete regression guard. An empty result must never be readable as proof of absence in
  // either renderer; this is the bug that motivated the whole parity finding.
  for (const [name, src] of [["stdio", STDIO], ["sse", SSE]]) {
    assert.match(src, /searched/, `${name} must report what it searched`);
    assert.match(src, /NOT searched/, `${name} must warn when messages were skipped`);
  }
});

console.log("transport-parity.test.js: all assertions passed");
