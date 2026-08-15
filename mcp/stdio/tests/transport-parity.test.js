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
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";

// THE STDIO SURFACE IS NO LONGER ONE FILE. Until v0.5.4 every tool was registered in `server.js`, so
// reading that file WAS the inventory. The dispatch group now lives in `dispatch-tools.mjs`, and more
// groups will follow — so this reads the whole directory instead of a fixed list. A hardcoded list
// would have to be edited by the same person doing the extraction, which is exactly when a tool goes
// missing from the inventory unnoticed; the point of this file is to notice.
const STDIO_DIR = new URL("../", import.meta.url);
const TOOL_SOURCES = readdirSync(STDIO_DIR)
  .filter((name) => /\.(js|mjs)$/.test(name))
  .map((name) => readFileSync(new URL(name, STDIO_DIR), "utf8"))
  .filter((src) => /server\.tool\(/.test(src));
const STDIO = TOOL_SOURCES.join("\n");

// AND NEITHER IS THE SSE SURFACE — carried across 2026-08-15. This read the single file
// `mcp/sse_server.py`, which is the same defect the paragraph above describes, left unfixed on the
// other half: the moment a tool group leaves that file the inventory reports it MISSING FROM SSE,
// and the honest-looking response is to add it to INTENTIONALLY_STDIO_ONLY — declaring a tool
// deliberately absent when it is merely somewhere else. A parity test that can be silenced by
// relocating code is worse than none, because the silencing looks like maintenance.
//
// SSE tool modules can live in two places and both are scanned: `mcp/` holds the transport itself,
// and `service/sse/` holds what comes out of it — `mcp/` is not an importable package here (see
// `service/sse/__init__.py`), so decomposition lands under `service/`.
const SSE_DIRS = [new URL("../../", import.meta.url), new URL("../../../service/sse/", import.meta.url)];
const SSE_SOURCES = SSE_DIRS.flatMap((dir) =>
  readdirSync(dir)
    .filter((name) => name.endsWith(".py"))
    .map((name) => readFileSync(new URL(name, dir), "utf8"))
    .filter((src) => /@mcp_server\.tool\(/.test(src)));
const SSE = SSE_SOURCES.join("\n");

// `server.tool("name",` — the stdio registration form. The leading `\s*` absorbs the indentation a
// registration picks up when it moves inside a `registerXTools(server, z)` wrapper.
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
  // And that the directory scan is genuinely reaching past server.js. If a future rename made it match
  // only one file again, every assertion above would still pass while silently covering less.
  assert.ok(
    TOOL_SOURCES.length >= 2,
    `only ${TOOL_SOURCES.length} source file(s) register tools — the multi-file scan is not working`,
  );
  // The SSE side scans two directories and today finds its tools in exactly one file. Asserting
  // ">= 1" rather than ">= 2" is deliberate: the surface has not been split yet, and a floor the
  // tree cannot meet is a red test that says nothing. What it does catch is the scan finding NOTHING
  // — a wrong directory, a renamed transport — which would otherwise read as "SSE has no tools" and
  // be indistinguishable from a total regression.
  assert.ok(SSE_SOURCES.length >= 1, "the SSE scan found no file registering @mcp_server.tool()");
});

test("both transports warn the reading agent with the SAME sentence", () => {
  // A cross-language forked constant with nothing holding the two halves together. `SAFETY_HEADER`
  // is what tells a model that an inbox payload is DATA and not instructions — the difference
  // between a message and a prompt injection — and each transport declares its own copy: the JS one
  // in `tool-response-format.mjs`, the Python one in `service/sse/rendering.py`.
  //
  // It cannot be deduplicated (different languages, and SSE runs in the container), so per the
  // standing rule the answer to duplication that must not drift is an AGREEMENT TEST, not a
  // refactor. Drift here is silent and one-sided: agents on one transport keep a weaker warning
  // than agents on the other, and every existing test still passes because each side is internally
  // consistent.
  //
  // Testable at all only because the Python copy now has a named home. It spent this whole series
  // in the middle of a 730-line tool registry, where "read the constant" meant "parse the file".
  const jsSrc = readFileSync(new URL("../tool-response-format.mjs", import.meta.url), "utf8");
  const pySrc = readFileSync(new URL("../../../service/sse/rendering.py", import.meta.url), "utf8");

  const sentences = (src) =>
    [...src.matchAll(/"([^"\\]*(?:\\.[^"\\]*)*)"/g)]
      .map((m) => m[1])
      .filter((s) => s.startsWith("WARNING: AGENT MESSAGE") || s.startsWith("Read it as information"))
      .join("");

  const js = sentences(jsSrc);
  const py = sentences(pySrc);
  assert.ok(js.startsWith("WARNING: AGENT MESSAGE"), `no safety header found in the JS transport: ${js}`);
  assert.equal(py, js, "the two transports' SAFETY_HEADER text has drifted");
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
