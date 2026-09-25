// Each MCP tool is registered by one module, and each exported name is declared by one module.
//
// WHAT THIS REPLACED. The v0.5.4 decomposition left behind one census per extraction: about twenty
// tests that each typed the names ONE slice moved and asserted `server.js` "kept none" of them, or that
// `declaringModules(name)` deepEquals one hand-written owner. Each covered the names somebody remembered
// on the day of that slice. A tool or a helper extracted since then had no census, and a leftover copy
// in any module other than `server.js` was never looked for. Both questions are asked here once, over a
// population derived from the source.
//
// WHY BOTH MATTER. A tool registered twice silently takes the second handler, so a stale copy shadows
// the live one. A name EXPORTED by its owner and declared again elsewhere keeps working until the two
// definitions disagree -- the shape the two build tags took.
//
// WHAT IT DOES NOT CATCH, measured rather than assumed (external review 2026-09-21, finding 10). The
// header used to cite `DelegatedManagedController` as an example, and that name is NOT caught: it is
// declared in `controllers/codex-controller.js` and `controllers/hermes-controller.js` without
// `export`, and this gate only considers names in the exported union, so the probe returns null for
// it. Nor does it catch a name declared TWICE IN ONE FILE, which the old per-slice `declaringModules`
// census did -- `declaredNames` collects into a Set, so one file appears once however many times it
// declares the name. Two module-private classes sharing a name is legal and common, so widening to
// every declaration would fire on ordinary code; the honest position is that this gate covers the
// EXPORTED surface, and the example it cites is one it actually holds.

import assert from "node:assert/strict";
import test from "node:test";

import { bridgeSources, declaredNames } from "./bridge-sources.mjs";
import { exportedNames } from "./missing-imports.mjs";
import { registerAllTools } from "../register-tools.mjs";

// ---- the two predicates, pure over [file, source] pairs -------------------------------------------

/** tool name -> the modules that call `server.tool("<name>"`, plus the call sites no name could be read from. */
export function toolRegistrations(sources) {
  const byName = new Map();
  const unnamed = [];
  for (const [file, src] of sources) {
    const calls = (src.match(/server\.tool\(/g) || []).length;
    const named = [...src.matchAll(/server\.tool\(\s*"([\w-]+)"/g)].map((m) => m[1]);
    if (named.length !== calls) unnamed.push(file);
    for (const name of named) byName.set(name, [...(byName.get(name) || []), file]);
  }
  return { byName, unnamed };
}

/** Exported names declared at top level by more than one module: name -> sorted declaring files. */
export function forkedExports(sources) {
  const exported = new Set();
  const declaredBy = new Map();
  for (const [file, src] of sources) {
    for (const name of exportedNames(src)) exported.add(name);
    for (const name of declaredNames(src)) declaredBy.set(name, [...(declaredBy.get(name) || []), file]);
  }
  const forks = {};
  for (const name of [...exported].sort()) {
    const files = declaredBy.get(name) || [];
    if (files.length > 1) forks[name] = [...files].sort();
  }
  return forks;
}

// ---- the tools --------------------------------------------------------------------------------------

function registeredAtRuntime() {
  const names = [];
  const server = new Proxy({}, {
    get: () => (...args) => { if (typeof args[0] === "string") names.push(args[0]); return server; },
  });
  const z = new Proxy(function () {}, { get: () => z, apply: () => z });
  registerAllTools(server, z, { ensureDispatchLoop() {} });
  return names;
}

test("every server.tool call names its tool, so the census below can see it", () => {
  assert.deepEqual(toolRegistrations(bridgeSources()).unnamed, [],
    "a registration whose name is not a string literal is invisible to this gate");
});

test("no tool is registered by two modules", () => {
  const twice = [...toolRegistrations(bridgeSources()).byName].filter(([, files]) => files.length > 1);
  assert.deepEqual(twice, [], "the second registration wins, so a stale copy shadows the live handler");
});

test("every tool the source registers is registered by registerAllTools, and nothing else is", () => {
  // The half a static census cannot see: a group module whose wrapper the registrar stopped calling.
  // Its tools still read as registered once, and agents get "unknown tool".
  const inSource = [...toolRegistrations(bridgeSources()).byName.keys()].sort();
  const atRuntime = registeredAtRuntime().sort();
  assert.deepEqual(atRuntime, inSource);
  assert.ok(inSource.length >= 30, `only ${inSource.length} tools found; the scan is not reading the bridge`);
});

test("NEGATIVE CONTROL: a duplicate, and an unnamed call, are both reported", () => {
  const { byName, unnamed } = toolRegistrations([
    ["a.mjs", 'server.tool(\n  "comms_x",\n  "desc", {}, h);'],
    ["server.js", 'server.tool("comms_x", "stale", {}, h);'],
    ["b.mjs", "server.tool(NAME, 'desc', {}, h);"],
  ]);
  assert.deepEqual(byName.get("comms_x"), ["a.mjs", "server.js"]);
  assert.deepEqual(unnamed, ["b.mjs"]);
});

// ---- the exported names -----------------------------------------------------------------------------

// MEASURED 2026-09-18 over the bridge population, and allowed to SHRINK ONLY. Each entry is an exported
// name some other module declares its own copy of. Most are deliberate -- `claude-channel.js` runs as a
// standalone process with its own endpoint constants, `MACHINE_ID` is `os.hostname()` in nine places --
// but none is proven harmless here, which is why the list is exact: a NEW module joining any entry (a
// leftover in server.js, say) goes red, and removing a duplicate goes red until its entry is deleted.
const KNOWN_FORKS = {
  ANSI: ["hermes-acp-protocol.js", "pi-terminal-frame.mjs"],
  API_KEY: ["aify-service-endpoint.mjs", "claude-channel.js", "notify-check.js"],
  CHANNEL_BRIDGE_PREFIX: ["claude-channel.js", "hermes-run-reporting.mjs"],
  HTTP_TIMEOUT_MS: ["aify-http.mjs", "aify-service-endpoint.mjs", "claude-channel.js"],
  MACHINE_ID: ["agent-heartbeat.mjs", "agent-summary.mjs", "auto-registration.mjs", "claude-channel.js",
    "hermes-env.mjs", "registration-tool.mjs", "resident-lost.mjs", "run-controls.mjs", "server.js"],
  MAX_CREDENTIAL_BYTES: ["credential-custody.mjs", "registry-credential.mjs"],
  SERVER_URL: ["aify-service-endpoint.mjs", "claude-channel.js", "doctor.js", "notify-check.js"],
  SERVER_URLS: ["aify-service-endpoint.mjs", "claude-channel.js"],
  TMP_DIR: ["claude-channel.js", "hermes-env.mjs"],
  channelBridgeId: ["claude-channel.js", "hermes-run-reporting.mjs"],
  coerceLoopbackToIPv4: ["aify-http.mjs", "aify-service-endpoint.mjs"],
  colorize: ["hermes-acp-protocol.js", "pi-terminal-frame.mjs"],
  createDeferred: ["codex-session.js", "hermes-managed-gateway-session.js", "hermes-session.js", "pi-session-timeouts.mjs"],
  defaultGetCmdline: ["hermes-daemon.js", "reap-managed-claude.js"],
  defaultKillTree: ["hermes-daemon.js", "proc-probes.js"],
  httpCall: ["aify-service-endpoint.mjs", "claude-channel.js"],
  idleTimeoutFor: ["codex-session.js", "hermes-session.js", "pi-session-timeouts.mjs"],
  main: ["bridge-main.mjs", "claude-session-hook.js", "claude-stop-gate.js", "codex-hook-trust.mjs"],
  normalizeRuntime: ["runtime-markers.js", "runtimes.js"],
  parseProcLines: ["proc-probes.js", "reap-managed-claude.js"],
  readBoundAgentId: ["claude-channel.js", "doctor-predicates.js", "hermes-managed-host.js"],
  reportResidentRuntimeLost: ["resident-runtime-lost.mjs", "server.js"],
  reportTurnBusy: ["agent-heartbeat.mjs", "claude-channel.js", "hermes-run-reporting.mjs"],
  runCli: ["hermes-managed-host.js", "reap-managed-claude.js"],
  sanitizeAgentId: ["claude-session-store.js", "hermes-endpoint.js"],
  sleep: ["claude-channel.js", "hermes-gateway.mjs", "server.js"],
  startupTimeoutFor: ["codex-session.js", "hermes-session.js", "pi-session-timeouts.mjs"],
};

test("no exported name gains a second declaration, and the known forks only shrink", () => {
  assert.deepEqual(forkedExports(bridgeSources()), KNOWN_FORKS,
    "a module declares its own copy of a name another module exports. Import it from the owner; "
    + "if a known fork was removed, delete its entry here");
});

test("NEGATIVE CONTROL: a redeclared export is reported; imports, re-exports and destructures are not", () => {
  const forks = forkedExports([
    ["owner.mjs", "export function helper() {}\nexport const LIMIT = 3;\nconst local = 1;\nexport { local as renamed };"],
    ["server.js", "import { LIMIT } from './owner.mjs';\nasync function helper() {}\nconst { renamed } = make();"],
    ["reexport.mjs", "export { helper } from './owner.mjs';"],
    ["private.mjs", "function unrelated() {}\nconst local = 2;"],
  ]);
  assert.deepEqual(forks, { helper: ["owner.mjs", "server.js"] });
});

test("POSITIVE CONTROL: the scan reads the real exports and declarations", () => {
  const sources = bridgeSources();
  assert.ok(sources.length > 150, `only ${sources.length} bridge modules found (181 on 2026-09-18)`);
  const safeName = sources.find(([file]) => file === "safe-name.mjs")[1];
  assert.ok(exportedNames(safeName).has("validateName"), "a known export was not read");
  assert.ok(declaredNames(safeName).has("validateName"), "a known declaration was not read");
  const exported = sources.reduce((n, [, src]) => n + exportedNames(src).size, 0);
  assert.ok(exported > 500, `only ${exported} exported names found across the bridge`);
});
