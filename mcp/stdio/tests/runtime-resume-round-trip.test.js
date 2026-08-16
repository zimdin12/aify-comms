// Every adapter declares the command that RESUMES it. Nothing checked that anything can UNDO one.
//
// `adapter-contract-symmetry.test.js` walks every registered adapter and requires a
// `resumeCommand(sessionId)` returning a command that embeds the id. That is one half of a
// round-trip. The other half lives in `runtimes.js`: `extractRuntimeSessionHandleFromCommand` reads
// the id back out of a command, and `runtimeCommandWithoutResume` removes it so a dead session can
// be relaunched fresh. Those two were driven by a HAND-TYPED runtime list — `key === "pi" ||
// key === "hermes" || key === "claude-code"` — with no connection to the adapters at all.
//
// Two of the five adapters were outside it, and the failure is silent in the shape this repo keeps
// paying for: `runtimeCommandWithoutResume` returns the command UNCHANGED for an unlisted runtime
// rather than raising. So
//
//   * the session heal in `terminal-runtime.js` only proceeds when the stripped command DIFFERS from
//     the original — `if (freshCommand && freshCommand !== state.command)`. For codex and opencode it
//     never could. A terminal whose saved handle is unresumable is not healed, is not reported as
//     un-healable, and simply stays dead.
//   * `extractTerminalSessionHandle` (server.js, the terminal START control path) returned "" for a
//     command with the handle plainly in it, so `terminalChildEnv` handed the worker an empty
//     `CODEX_THREAD_ID` and `AIFY_SESSION_HANDLE`.
//
// HOW LIVE IS IT, honestly: latent, and worth fixing anyway. The dashboard renders codex resumes in
// the SUBCOMMAND form (`codex --no-alt-screen resume --include-non-interactive <handle>`), which the
// old regex did match, and `classifyTerminalRuntimeOutput` only returns `missing_session` for pi and
// hermes, so the heal path cannot currently reach a codex terminal at all. What was broken is the
// operator/wrapper spelling — `codex-aify --resume <id>`, which install.sh's codex branch parses and
// which is exactly what `adapters/codex.js` hands the operator as the takeover command. The defect
// was waiting for its first caller.
//
// THE GATE BELOW IS THE DURABLE PART. It feeds each adapter's OWN `resumeCommand()` back through
// the stripper and the extractor, so the two enumerations cannot drift again: add an adapter, or
// change a `resumeCommand`, and the runtime that cannot undo it fails here by name.
//
// No opencode process is started anywhere in this file. These are pure string functions.

import assert from "node:assert/strict";
import test from "node:test";

import { adapterFor, supportedRuntimes } from "../adapters/index.js";
import {
  RUNTIME_SESSION_ENV_VARS,
  extractRuntimeSessionHandleFromCommand,
  normalizeRuntime,
  runtimeCommandWithoutResume,
  sessionEnvVarsForRuntime,
} from "../runtimes.js";
import { terminalChildEnv } from "../terminal-env.js";
import { terminalEnvWithoutResume } from "../terminal-text.js";

// Enumerate the REGISTRY, never a hardcoded list — the same rule
// `adapter-contract-symmetry.test.js` states: "A new entry in the registry's Map is picked up
// here with zero test changes." A hardcoded list is how codex and opencode went unnoticed.
const ADAPTER_RUNTIMES = supportedRuntimes();

test("the adapter population is real — an empty registry would make the gate below vacuous", () => {
  assert.ok(ADAPTER_RUNTIMES.length >= 5, `only ${ADAPTER_RUNTIMES.length} adapters registered`);
  for (const runtime of ["claude-code", "codex", "hermes", "opencode", "pi"]) {
    assert.ok(ADAPTER_RUNTIMES.includes(runtime), `${runtime} missing from the adapter registry`);
  }
});

test("EVERY adapter's own resumeCommand can be stripped and read back", () => {
  const id = "SID-42";
  for (const runtime of ADAPTER_RUNTIMES) {
    const command = adapterFor(runtime).resumeCommand(id);
    assert.equal(
      extractRuntimeSessionHandleFromCommand(runtime, command), id,
      `${runtime}: the handle cannot be read back out of its OWN resume command ${JSON.stringify(command)}. `
      + "The terminal start path derives the worker's session env from this — an unreadable command "
      + "gives the worker an empty session handle.",
    );
    const stripped = runtimeCommandWithoutResume(runtime, command);
    assert.ok(
      !stripped.includes(id),
      `${runtime}: ${JSON.stringify(command)} still carries the session id after stripping `
      + `(${JSON.stringify(stripped)}).`,
    );
    assert.notEqual(
      stripped, command,
      `${runtime}: the stripped command is IDENTICAL to the original, which is how this fails in `
      + "production — terminal-runtime.js only heals when the two differ, so an unresumable session "
      + "is silently never restarted.",
    );
    assert.ok(stripped.trim(), `${runtime}: stripping removed the whole command, not just the flag`);
  }
});

test("the forms that already worked still produce the same answers", () => {
  // Regression cover for the three runtimes whose flag set is unchanged, plus codex's SUBCOMMAND
  // spelling — the one the dashboard actually renders, and the one the old single regex handled.
  const cases = [
    ["codex", "codex --no-alt-screen resume --include-non-interactive 01ABC --model gpt",
      "codex --no-alt-screen --model gpt", "01ABC"],
    ["codex", "codex-aify resume 01ABC", "codex-aify", "01ABC"],
    ["claude-code", "claude-aify --resume sess-1 --model opus", "claude-aify --model opus", "sess-1"],
    ["claude-code", "claude-aify -r sess-1", "claude-aify", "sess-1"],
    ["claude-code", "claude-aify --session-id=sess-1 --model opus", "claude-aify --model opus", "sess-1"],
    ["hermes", "hermes-aify --resume h9", "hermes-aify", "h9"],
    ["pi", `pi --resume "sess 1" --model x`, "pi --model x", "sess 1"],
  ];
  for (const [runtime, command, expectedStripped, expectedHandle] of cases) {
    assert.equal(runtimeCommandWithoutResume(runtime, command), expectedStripped, `${runtime}: ${command}`);
    assert.equal(extractRuntimeSessionHandleFromCommand(runtime, command), expectedHandle, `${runtime}: ${command}`);
  }
});

test("codex accepts the two flags its wrapper parses, and NOT the -r the others take", () => {
  // Not a style choice. install.sh's codex branch matches `--resume` and `--session-id` only; `-r`
  // belongs to claude-aify. A regex that stripped `-r` from a codex command would delete a real
  // argument, so the flag sets are per-runtime rather than one shared alternation.
  assert.equal(runtimeCommandWithoutResume("codex", "codex-aify --resume T1"), "codex-aify");
  assert.equal(runtimeCommandWithoutResume("codex", "codex-aify --session-id T1"), "codex-aify");
  assert.equal(runtimeCommandWithoutResume("codex", "codex-aify --resume=T1"), "codex-aify");
  assert.equal(
    runtimeCommandWithoutResume("codex", "codex-aify -r T1"), "codex-aify -r T1",
    "codex-aify does not parse -r; stripping it would silently drop an argument codex was given",
  );
  assert.equal(extractRuntimeSessionHandleFromCommand("codex", "codex-aify -r T1"), "");
});

test("a runtime with no resume syntax passes the command through untouched", () => {
  // `generic` has no adapter entry and no flag set. The pass-through is deliberate — but it is also
  // exactly what made the codex and opencode gaps invisible, so it is pinned rather than assumed.
  assert.equal(runtimeCommandWithoutResume("generic", "anything --resume s3"), "anything --resume s3");
  assert.equal(extractRuntimeSessionHandleFromCommand("generic", "anything --resume s3"), "");
  assert.equal(runtimeCommandWithoutResume("", ""), "");
  assert.equal(extractRuntimeSessionHandleFromCommand("", ""), "");
});

test("a quoted handle survives the round trip, and an unterminated quote does not crash", () => {
  assert.equal(extractRuntimeSessionHandleFromCommand("pi", `pi --resume 'sess 1'`), "sess 1");
  assert.equal(extractRuntimeSessionHandleFromCommand("pi", `pi --resume "sess 1"`), "sess 1");
  assert.equal(runtimeCommandWithoutResume("pi", `pi --resume 'sess 1' --model x`), "pi --model x");
  assert.doesNotThrow(() => runtimeCommandWithoutResume("pi", `pi --resume "unterminated`));
});

test("the session env-var table names every runtime that has an adapter, and only those", () => {
  // `sessionEnvVarsForRuntime` returns [] for anything unlisted, so a runtime missing from the table
  // silently gets no session variables rather than failing.
  assert.deepEqual(
    Object.keys(RUNTIME_SESSION_ENV_VARS).sort(), ADAPTER_RUNTIMES.slice().sort(),
    "the session env-var table and the adapter registry disagree about which runtimes exist",
  );
  for (const runtime of ADAPTER_RUNTIMES) {
    assert.deepEqual(
      sessionEnvVarsForRuntime(runtime), adapterFor(runtime).sessionEnvVars,
      `${runtime}: the table disagrees with the adapter's own sessionEnvVars`,
    );
    assert.ok(sessionEnvVarsForRuntime(runtime).length, `${runtime} has no session env vars`);
  }
  assert.deepEqual(sessionEnvVarsForRuntime("generic"), [], "generic has no session identity");
  assert.deepEqual(sessionEnvVarsForRuntime("nonsense-runtime"), [], "an unknown runtime must not throw");
  // Aliases resolve before lookup — `oh-my-pi` is pi.
  assert.deepEqual(sessionEnvVarsForRuntime("oh-my-pi"), sessionEnvVarsForRuntime("pi"));
  assert.equal(normalizeRuntime("oh-my-pi"), "pi");
});

test("a launched worker's env carries the handle, and the no-resume strip removes every trace", () => {
  // The contract that makes a heal actually FRESH: whatever `terminalChildEnv` seeds for a runtime,
  // `terminalEnvWithoutResume` must be able to remove — both read the same table, and this is what
  // proves they agree for all six runtimes rather than for the one someone tested by hand.
  const handle = "HANDLE-XYZ";
  for (const runtime of [...ADAPTER_RUNTIMES, "generic"]) {
    const env = terminalChildEnv({
      baseEnv: {},
      runtime,
      sessionHandle: handle,
      workspace: "/w",
      terminal: { agentId: "a" },
      prepareCodexHome: () => "/codex-home",
    });
    for (const name of sessionEnvVarsForRuntime(runtime)) {
      assert.equal(env[name], handle, `${runtime}: ${name} must carry the handle into the worker`);
    }
    const stripped = terminalEnvWithoutResume(runtime, env);
    const leaks = Object.entries(stripped).filter(([, value]) => String(value) === handle).map(([k]) => k);
    assert.deepEqual(
      leaks, [],
      `${runtime}: ${leaks.join(", ")} still name the old session after the no-resume strip, so the `
      + "healed worker resumes the session it was restarted to escape",
    );
  }
});

test("AIFY_RUNTIME is always set, which is what keeps inherited session vars inert", () => {
  // MEASURED, not assumed: `...baseEnv` spreads the ENVIRONMENT BRIDGE's own environment, so a
  // codex worker really does inherit the bridge's CLAUDE_SESSION_ID, PI_SESSION_ID and friends —
  // `terminalChildEnv` only overwrites the vars belonging to the worker's OWN runtime. That is the
  // same leak the AIFY_AGENT_ROLE fix was written for ("an inherited one makes it confidently
  // wrong"), and it is harmless here for one reason only: every consumer is runtime-scoped, and
  // `detectRuntime` never has to sniff those variables because AIFY_RUNTIME is set explicitly.
  //
  // So the reason is the assertion. If AIFY_RUNTIME ever stops being set, detectRuntime falls
  // through to env sniffing and the inherited handles start deciding what a worker thinks it is.
  const bridgeEnv = {
    CLAUDE_SESSION_ID: "bridge-claude",
    PI_SESSION_ID: "bridge-pi",
    HERMES_SESSION: "bridge-hermes",
    OPENCODE_SESSION: "bridge-oc",
  };
  for (const runtime of [...ADAPTER_RUNTIMES, "generic"]) {
    const env = terminalChildEnv({
      baseEnv: bridgeEnv,
      runtime,
      sessionHandle: "H",
      workspace: "/w",
      terminal: { agentId: "a" },
      prepareCodexHome: () => "/codex-home",
    });
    assert.equal(
      env.AIFY_RUNTIME, normalizeRuntime(runtime),
      `${runtime}: AIFY_RUNTIME must be set explicitly — without it detectRuntime() sniffs the `
      + "session variables this worker INHERITED from the environment bridge",
    );
  }
});
