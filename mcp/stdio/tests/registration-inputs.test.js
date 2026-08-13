// What a registration resolves before it registers — and the two operator-reported defects it exists to
// prevent.
//
// Both are the same shape: a value that LOOKS resolved reaches the backend agent record, and the agent then
// registers successfully and fails later, somewhere else, for a reason that does not name the cause.
//
//   * A backslash cwd. The runtime marker key is `sha256(cwd)`, so `C:\foo` and `C:/foo` are two different
//     agents to Codex. A registration that stores one while the wrapper wrote the other looks fine until
//     dispatch cannot find the marker.
//   * An unexpanded `${AIFY_HERMES_GATEWAY_URL}`. Reported 2026-05-25: hermes' own config.yaml interpolates
//     the placeholder to its literal text when the variable is unset, and a literal placeholder is a truthy
//     string. Stored as a gateway URL it makes the resident-channel controller fail at connect time.
//
// None of this was reachable from a test before the extraction: it lived in `server.js`, the bin entry
// point, which nothing imports.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs, { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  DEFAULT_CWD,
  claimCapturedClaudeSession,
  normalizeRegistrationCwd,
  resolvedRuntimeConfigForRegistration,
  resolvedRuntimeMarker,
} from "../registration-inputs.mjs";
import { AIFY_HERMES_GATEWAY_URL } from "../hermes-gateway-config.mjs";
import { bridgeSources, declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "registration-inputs.mjs")).href;

// Run one expression against a freshly imported leaf under the given env. Needed only where a value is
// captured at MODULE LOAD rather than read per call — `hermes-gateway-config.mjs` resolves its exports once,
// so an in-process `process.env` change cannot reach it.
function withEnv(env, expr) {
  const script = `
    const m = await import(${JSON.stringify(LEAF)});
    process.stdout.write(JSON.stringify(${expr}));
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
    env: { ...process.env, ...env }, encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
  }));
}

test("A BACKSLASH CWD IS NORMALIZED FOR THE RUNTIMES WHOSE MARKER KEY IS sha256(cwd)", () => {
  // The whole reason the function exists. Codex's Rust path deserializer rejects mixed separators, and the
  // marker lookup hashes the string — so an un-normalized cwd is not a cosmetic difference, it is a
  // different agent.
  if (process.platform === "win32") {
    assert.equal(normalizeRegistrationCwd("codex", "C:\\Docker\\aify-comms"), "C:/Docker/aify-comms");
    assert.equal(normalizeRegistrationCwd("claude-code", "C:\\a\\b"), "C:/a/b");
    // A forward-slash cwd must pass through UNCHANGED, or the two spellings still disagree — from the other
    // side. This is the half that makes it a normalization rather than a translation.
    assert.equal(normalizeRegistrationCwd("codex", "C:/a/b"), "C:/a/b");
    // NOT normalized for the others. Current behaviour and deliberate: only these two runtimes key on the
    // hash, and hermes/pi cwds are passed to tools that accept the native separator.
    assert.equal(normalizeRegistrationCwd("hermes", "C:\\a\\b"), "C:\\a\\b");
    assert.equal(normalizeRegistrationCwd("generic", "C:\\a\\b"), "C:\\a\\b");
  } else {
    // The rewrite is gated on `process.platform === "win32"` in the source. On POSIX a backslash is a legal
    // filename character, so rewriting it would corrupt a real path.
    assert.equal(normalizeRegistrationCwd("codex", "/home/x/y"), "/home/x/y");
  }
});

test("an absent cwd falls back to the process's, never to empty", () => {
  // An empty cwd would hash to a marker no wrapper ever wrote, so the agent would register with a marker key
  // that cannot match. Every degenerate spelling of "no cwd" must land on the same fallback.
  for (const absent of [undefined, null, "", "   ", 0, false]) {
    assert.equal(normalizeRegistrationCwd("hermes", absent), DEFAULT_CWD,
      `a cwd of ${JSON.stringify(absent)} must fall back to the process cwd`);
  }
  assert.equal(DEFAULT_CWD, process.cwd(), "and the fallback is this process's own directory");
  assert.ok(DEFAULT_CWD, "…which must never be empty");
});

test("AN UNEXPANDED ${...} GATEWAY URL IS REJECTED, not stored", () => {
  // The 2026-05-25 operator report. `${AIFY_HERMES_GATEWAY_URL}` is truthy, so a plain falsy-check would
  // have passed it straight through into runtime_config.
  assert.equal(AIFY_HERMES_GATEWAY_URL, "",
    "this test drives the env fallback, which the imported binding takes precedence over");
  const prev = process.env.AIFY_HERMES_GATEWAY_URL;
  try {
    process.env.AIFY_HERMES_GATEWAY_URL = "${AIFY_HERMES_GATEWAY_URL}";
    const cfg = resolvedRuntimeConfigForRegistration("hermes", null, "C:/x");
    assert.equal("gatewayUrl" in cfg, false, "an unexpanded placeholder must not become a gateway URL");

    // What a REAL value does, so the test above cannot pass by the function rejecting everything.
    process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.2:9999/gw";
    assert.equal(resolvedRuntimeConfigForRegistration("hermes", null, "C:/x").gatewayUrl,
      "ws://127.0.0.2:9999/gw", "a well-formed ws:// URL must be stored");
    process.env.AIFY_HERMES_GATEWAY_URL = "wss://host/gw";
    assert.equal(resolvedRuntimeConfigForRegistration("hermes", null, "C:/x").gatewayUrl, "wss://host/gw",
      "…and wss:// too, since that is what a real deployment uses");

    // The gate is a scheme check, not a placeholder check. Anything that is not ws/wss is refused — an
    // https:// gateway URL is a misconfiguration that would fail at connect, so it is better refused here.
    for (const bad of ["https://host/gw", "host/gw", "${OTHER}", "  "]) {
      process.env.AIFY_HERMES_GATEWAY_URL = bad;
      assert.equal("gatewayUrl" in resolvedRuntimeConfigForRegistration("hermes", null, "C:/x"), false,
        `${JSON.stringify(bad)} is not a ws:// URL and must not be stored`);
    }
  } finally {
    if (prev === undefined) delete process.env.AIFY_HERMES_GATEWAY_URL;
    else process.env.AIFY_HERMES_GATEWAY_URL = prev;
  }
});

test("the module-load binding WINS over the env fallback", () => {
  // The precedence the test above depends on, asserted rather than assumed. `AIFY_HERMES_GATEWAY_URL` is
  // read from `hermes-gateway-config.mjs` first and only falls through when empty, so a bridge launched with
  // a gateway configured cannot have it overridden by a later env change.
  const cfg = withEnv(
    { AIFY_HERMES_GATEWAY_URL: "ws://from-load/gw" },
    `m.resolvedRuntimeConfigForRegistration("hermes", null, "C:/x")`,
  );
  assert.equal(cfg.gatewayUrl, "ws://from-load/gw");
});

test("THE CWD MARKER IS THE LAST RESORT — this process's own gateway outranks it", () => {
  // The precedence `AIFY_HERMES_GATEWAY_URL || process.env.… || marker?.gatewayUrl`, proven end to end.
  //
  // It matters because cwd-keyed markers COLLIDE: two hermes agents in the same folder hash to the same
  // marker key, so a registration that trusted the marker first could adopt the other agent's gateway. This
  // used to be asserted as a regex over `server.js` in `hermes-register-fresh-handle.test.js`, which broke
  // the moment the code moved here — and which could only ever prove the line was written, not that the
  // precedence held.
  //
  // The marker is written by the REAL `writeRuntimeMarker` under an isolated `XDG_STATE_HOME`, so this
  // touches nothing in the operator's own `~/.local/state/aify-comms/runtime-markers`.
  const root = path.join(os.tmpdir(), `aify-marker-test-${process.pid}`);
  const cwd = "C:/aify-marker-precedence-test";
  const MARKERS = pathToFileURL(path.join(STDIO, "runtime-markers.js")).href;
  const run = (gatewayEnv) => {
    const script = `
      const { writeRuntimeMarker } = await import(${JSON.stringify(MARKERS)});
      const m = await import(${JSON.stringify(LEAF)});
      const wrote = writeRuntimeMarker("hermes", ${JSON.stringify(cwd)}, { gatewayUrl: "ws://from-marker/gw" });
      process.stdout.write(JSON.stringify({
        wrote: Boolean(wrote),
        seen: m.resolvedRuntimeMarker("hermes", ${JSON.stringify(cwd)})?.gatewayUrl || null,
        config: m.resolvedRuntimeConfigForRegistration("hermes", null, ${JSON.stringify(cwd)}),
      }));
    `;
    return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
      env: { ...process.env, XDG_STATE_HOME: root, AIFY_HERMES_GATEWAY_URL: gatewayEnv },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
    }));
  };
  try {
    // With no gateway in the environment, the marker supplies it — the fallback must actually work, or the
    // precedence test below would pass for the wrong reason.
    const viaMarker = run("");
    assert.equal(viaMarker.wrote, true, "the fixture must really have written a marker");
    assert.equal(viaMarker.seen, "ws://from-marker/gw", "…and the resolver must really read it back");
    assert.equal(viaMarker.config.gatewayUrl, "ws://from-marker/gw",
      "with nothing in the environment the cwd marker supplies the gateway");

    // Now the property: the same marker is present, but this process has its own gateway and that wins.
    const viaEnv = run("ws://mine/gw");
    assert.equal(viaEnv.seen, "ws://from-marker/gw", "the marker is still there…");
    assert.equal(viaEnv.config.gatewayUrl, "ws://mine/gw",
      "…and must be outranked by this MCP process's own gateway");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("A STALE KEY FROM THE PREVIOUS REGISTRATION IS DELETED, not carried forward", () => {
  // Re-registration is a full state refresh. If the previous record had an appServerUrl and this
  // registration resolves none, carrying it forward would point dispatch at an app-server that is gone —
  // the config would describe the PREVIOUS launch. Every runtime branch has an `else delete` for this and
  // each one is load-bearing.
  const prevCodex = process.env.AIFY_CODEX_APP_SERVER_URL;
  try {
    delete process.env.AIFY_CODEX_APP_SERVER_URL;
    const stale = { runtimeConfig: JSON.stringify({ appServerUrl: "http://old:1", keepMe: "yes" }) };
    const cfg = resolvedRuntimeConfigForRegistration("codex", stale, "C:/x");
    assert.equal("appServerUrl" in cfg, false, "a resolved-to-nothing appServerUrl must be dropped");
    // …but an unrelated key survives: this is a refresh of what the resolver owns, not a wipe.
    assert.equal(cfg.keepMe, "yes", "keys this resolver does not own must be preserved");

    process.env.AIFY_CODEX_APP_SERVER_URL = "http://new:2";
    assert.equal(resolvedRuntimeConfigForRegistration("codex", stale, "C:/x").appServerUrl, "http://new:2",
      "a newly resolved value must replace the old one");
  } finally {
    if (prevCodex === undefined) delete process.env.AIFY_CODEX_APP_SERVER_URL;
    else process.env.AIFY_CODEX_APP_SERVER_URL = prevCodex;
  }

  // Same property on the claude-code branch, whose value is a boolean flag rather than a string.
  const wasEnabled = { runtimeConfig: JSON.stringify({ channelEnabled: true }) };
  assert.equal("channelEnabled" in resolvedRuntimeConfigForRegistration("claude-code", wasEnabled, "C:/x"),
    false, "channelEnabled must be dropped when no marker reports the channel");
});

test("a malformed previous runtimeConfig degrades to empty rather than throwing", () => {
  // `previousInfo.runtimeConfig` is whatever the backend stored. A registration must not fail because an
  // older record is unparseable — that would make a corrupt row permanently unregisterable.
  for (const bad of ["{not json", "", null, undefined, "[]", "null", 42]) {
    const cfg = resolvedRuntimeConfigForRegistration("generic", { runtimeConfig: bad }, "C:/x");
    assert.equal(typeof cfg, "object", `runtimeConfig ${JSON.stringify(bad)} must still yield an object`);
    assert.ok(cfg !== null);
  }
  assert.deepEqual(resolvedRuntimeConfigForRegistration("generic", null, "C:/x"), {},
    "no previous info at all is the ordinary first-registration case");
});

test("resolvedRuntimeMarker answers null rather than guessing when the answer is ambiguous", () => {
  // With no marker written for a cwd there is no live wrapper to bind to. Returning a fabricated marker
  // would bind the agent to a wrapper that does not exist.
  for (const rt of ["codex", "claude-code", "hermes", "generic", "pi"]) {
    assert.equal(resolvedRuntimeMarker(rt, "C:/nonexistent-marker-dir-for-test"), null,
      `${rt} must report no marker rather than inventing one`);
  }
});

test("claimCapturedClaudeSession refuses a blank agent id and never throws", () => {
  // It runs on the registration path, so a throw here fails the registration itself. Every failure mode is
  // swallowed into `false` by design.
  for (const blank of ["", "   ", null, undefined, 0]) {
    assert.equal(claimCapturedClaudeSession(blank), false,
      `a blank agent id (${JSON.stringify(blank)}) must not claim a session`);
  }
  assert.equal(claimCapturedClaudeSession("agent-that-has-no-captured-session"), false);
});

test("exactly one module declares each, and server.js still uses them", () => {
  for (const name of ["normalizeRegistrationCwd", "resolvedRuntimeMarker",
    "resolvedRuntimeConfigForRegistration", "claimCapturedClaudeSession"]) {
    assert.deepEqual(declaringModules(name), [{ file: "registration-inputs.mjs", kind: "function" }],
      `${name} must be declared exactly once, by its owner`);
  }
  // `binding`, not `const` — read off `declaringModules` after asserting the word I expected. The scanner
  // does not distinguish const/let/var, which is right: what matters is that ONE module declares the name.
  assert.deepEqual(declaringModules("DEFAULT_CWD"), [{ file: "registration-inputs.mjs", kind: "binding" }],
    "DEFAULT_CWD moved here rather than being captured twice — see the header note");

  // THE BRIDGE, not `server.js`. I wrote the file-named form three commits ago and it broke one slice later
  // when `autoRegisterConfiguredAgent` — the last server.js caller of `normalizeRegistrationCwd` — moved to
  // its own owner, with nothing about this module changed. An owner's contract is that SOMETHING still calls
  // it; which file does is the thing this series keeps changing on purpose.
  for (const name of ["normalizeRegistrationCwd", "resolvedRuntimeConfigForRegistration",
    "claimCapturedClaudeSession", "DEFAULT_CWD"]) {
    assert.ok(isUsedInBridge(name), `${name} must still be used by something — an unused owner is dead code`);
  }
  // `resolvedRuntimeMarker` is deliberately absent from that list: its only caller is
  // `resolvedRuntimeConfigForRegistration`, inside this same module, so nothing outside reaches it.
  assert.equal(
    bridgeSources().filter(([file, text]) =>
      file !== "registration-inputs.mjs" && /(?<![\w.])resolvedRuntimeMarker\(/.test(text)).length,
    0, "resolvedRuntimeMarker is internal to its owner; an outside caller means the surface changed",
  );
});

test("the owner holds no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO, "registration-inputs.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^} from "([^"]+)";$|^import .* from "([^"]+)";$/gm)]
    .map((m) => m[1] || m[2]).sort();
  assert.deepEqual(imports, [
    "./claude-session-store.js",
    "./hermes-gateway-config.mjs",
    "./parse-json.mjs",
    "./runtime-markers.js",
    "./runtimes.js",
  ], "a new import here is a new dependency for the registration path — review it");
});
