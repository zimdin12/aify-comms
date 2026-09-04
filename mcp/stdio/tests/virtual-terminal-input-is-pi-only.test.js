#!/usr/bin/env node
// Four runtimes get a synthesized terminal. Operator input on ANY of them acquires a PI session.
//
// `dispatchVirtualTerminalLine` was written for pi and says so: "Drive the persistent PiSession from
// operator-typed terminal input." It hardcodes the runtime in both places it matters —
// `acquirePiSession({...})` and `ensureVirtualTerminal(agentId, agentInfo, "pi")`.
//
// The ROUTING around it then grew to four runtimes and nothing revisited that:
//
//   dispatch-loop.mjs:368    creates a virtual terminal for pi, hermes, codex AND opencode,
//                            tagging the entry with the agent's REAL runtime
//   virtual-terminals.mjs    VIRTUAL_RPC_RUNTIMES = {pi, hermes, codex, opencode}, and
//                            findAgentIdForVirtualTerminal admits an entry whose runtime is any
//                            of them  <-- asserted below, by calling it
//   terminal-control-loop:52 routes that terminal's controls to handleVirtualTerminalControl with
//                            NO runtime check
//   virtual-terminals.mjs    action === "input" -> VIRTUAL_TERMINAL_INPUT.append -> dispatch ->
//                            dispatchVirtualTerminalLine -> acquirePiSession
//
// So an `input` control on a managed HERMES agent's synthesized terminal acquires a PI session for
// that agent, and `ensureVirtualTerminal(..., "pi")` then rewrites the cached entry's runtime from
// "hermes" to "pi". The `stop` branch is pi-specific too (`getPiSession`), though there it merely
// finds nothing.
//
// WHAT IS PROVEN HERE, AND WHAT IS NOT. The router admitting all four runtimes is proven by CALLING
// it. The pi-hardcoding and the absence of a runtime guard are read from source and pinned as text,
// because executing the dispatcher would acquire a real runtime session — not something a test may
// do. NOT established: whether the dashboard actually offers an input box for a synthesized
// hermes/codex/opencode console. The bridge-side path is open regardless of whether the UI walks it
// today, and a control can also arrive from the API directly.
//
// NOT A FIX. What input SHOULD do for a non-pi synthesized terminal is a product question — refuse
// it with a clear control response, or route it into that runtime's own delivery path — and the code
// does not answer it. Pinned so the answer is a decision.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  VIRTUAL_RPC_RUNTIMES,
  VIRTUAL_TERMINALS_BY_AGENT,
  findAgentIdForVirtualTerminal,
} from "../virtual-terminals.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceOf = (rel) => readFileSync(path.join(STDIO, rel), "utf-8").replace(/\r\n/g, "\n");

// ── the router admits every virtual-RPC runtime, not just pi ─────────────────────────────────
{
  const admitted = [];
  for (const runtime of ["pi", "hermes", "codex", "opencode", "claude-code"]) {
    VIRTUAL_TERMINALS_BY_AGENT.clear();
    VIRTUAL_TERMINALS_BY_AGENT.set(`agent-${runtime}`, {
      terminalId: `vterm_${runtime}`,
      runtime,
    });
    if (findAgentIdForVirtualTerminal(`vterm_${runtime}`)) admitted.push(runtime);
  }
  VIRTUAL_TERMINALS_BY_AGENT.clear();

  assert.deepEqual(
    admitted, ["pi", "hermes", "codex", "opencode"],
    "the set of runtimes whose synthesized terminal routes to the virtual-control handler changed. "
      + "Every one of them reaches an input branch that acquires a PI session.",
  );
  assert.ok(!admitted.includes("claude-code"), "claude uses a real PTY, never a synthesized one");
  assert.deepEqual(
    [...VIRTUAL_RPC_RUNTIMES].sort(), ["codex", "hermes", "opencode", "pi"],
    "VIRTUAL_RPC_RUNTIMES is what the router keys on; if it changed, so did the exposure",
  );
}

// ── and the dispatcher it reaches is pi-only ─────────────────────────────────────────────────
{
  const src = sourceOf("virtual-terminals.mjs");
  const start = src.indexOf("export async function dispatchVirtualTerminalLine");
  assert.ok(start > 0, "dispatchVirtualTerminalLine moved; repoint this test");
  const body = src.slice(start, src.indexOf("\nexport ", start + 10));

  assert.match(body, /acquirePiSession\(/, "the dispatcher acquires a PI session unconditionally");
  assert.match(
    body, /ensureVirtualTerminal\([^)]*,\s*"pi"\)/,
    'the dispatcher re-ensures the terminal as runtime "pi", overwriting a hermes/codex/opencode tag',
  );
  // `\b` matters: the dispatcher legitimately reads `agentInfo?.runtimeState?.sessionId` for the
  // session handle, and a looser pattern matches that and reports a runtime check that is not there.
  assert.doesNotMatch(
    body, /agentInfo\?\.runtime\b|normalizeRuntime|VIRTUAL_RPC_RUNTIMES/,
    "the dispatcher now consults the agent's runtime. If non-pi input is handled, this test should "
      + "be replaced by one asserting what it does instead.",
  );
}

// ── and nothing DOWNSTREAM refuses either, which is what sets the severity ───────────────────
{
  // The open question is "what should non-pi input do", and its cost depends on what
  // `acquirePiSession` does when handed a hermes agent. It does not refuse: no runtime check, and
  // it goes on to resolve the PI launcher and call `ensureStarted`, which is what starts the
  // `omp --mode rpc` child. So the consequence is a pi RUNTIME PROCESS started in a hermes agent's
  // cwd — not merely a bookkeeping entry. Asserted rather than described, so the basis for the
  // decision is checkable and so that a guard added here shows up as a changed exposure.
  const pool = sourceOf("pi-session-pool.mjs");
  const start = pool.indexOf("export async function acquirePiSession");
  assert.ok(start > 0, "acquirePiSession moved; repoint this test");
  const body = pool.slice(start, pool.indexOf("\nexport ", start + 10));

  // Look for a GUARD, not for the word: the body legitimately calls `getRuntimeConfig(agentInfo)`
  // to read pi's model/thinking config, and a bare /runtime/i matches that. This is the second
  // over-loose assertion of mine in this file — the first matched `agentInfo?.runtimeState`.
  assert.doesNotMatch(
    body, /normalizeRuntime|[!=]==\s*["'`]pi["'`]|["'`]pi["'`]\s*[!=]==/,
    "acquirePiSession now compares a runtime. If it refuses non-pi agents, the virtual-terminal "
      + "input exposure is smaller than this file describes — re-measure before relying on it.",
  );
  assert.match(body, /resolvePiLauncher\(\)/, "it resolves the PI launcher unconditionally");
  assert.match(body, /ensureStarted\(/, "...and starts the session, i.e. spawns the runtime child");
}

// ── nothing between the router and the dispatcher checks the runtime ─────────────────────────
{
  // THE ROUTER HALF WENT WITH THE ENVIRONMENT BRIDGE in v0.6.2. It read
  // `terminal-control-loop.mjs` to prove that nothing between the control loop and
  // `handleVirtualTerminalControl` checked the runtime. That loop is deleted, so the handler
  // asserted below is now the whole path rather than half of it.

  const virt = sourceOf("virtual-terminals.mjs");
  const handler = virt.slice(virt.indexOf("export async function handleVirtualTerminalControl"));
  const inputBranch = handler.slice(0, handler.indexOf('if (action === "resize")'));
  assert.doesNotMatch(
    inputBranch, /runtime/i,
    "the input branch now considers the runtime; update or delete this test",
  );
}

console.log("virtual-terminal-input-is-pi-only.test.js: all assertions passed");
