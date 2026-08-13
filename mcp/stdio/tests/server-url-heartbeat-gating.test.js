// One server URL, one precedence, one coercion — and heartbeats that do not run when there is no server.
//
// THE DEFECT THIS FIXES. `server.js` carried its own server-URL derivation, `__serverUrl`, alongside the
// endpoint leaf's `SERVER_URL`. It differed three ways: opposite env precedence, no IPv4 loopback coercion,
// and — the one that mattered — a DEFAULT of `http://127.0.0.1:8800`, which made it never empty. Eight
// callback guards were written as `if (!__serverUrl) return;`, intending "do nothing when no server is
// configured". They could not fire. Two heartbeat posters were handed that value as a base URL and fetch it
// directly rather than through `httpCall`, so a bridge with no configured service beat against the default
// port every 30 seconds for the lifetime of the process — and if anything else were listening there, that
// agent's session handle and turn-busy state went to it.
//
// SEVERITY, HONESTLY: `install.sh` sets both env vars explicitly in every wrapper and MCP config, so no
// supported install reached that state. The reachable half is the missing coercion — an operator who types
// `http://localhost:8800` at the install prompt got coerced tool calls and uncoerced heartbeats, on Windows
// with Docker Desktop, where the endpoint leaf's own comment says IPv6 loopback times out silently.
//
// These tests are source-level. The three heartbeat start sites are module-scope in `server.js`, which is the
// bin entry point and cannot be imported, so the wiring is asserted structurally and the URL semantics are
// asserted behaviourally against the endpoint leaf that now owns them.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { bridgeSources, declaringModules } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = readFileSync(path.join(STDIO, "server.js"), "utf-8");
const ENDPOINT = pathToFileURL(path.join(STDIO, "aify-service-endpoint.mjs")).href;

// Resolve the endpoint leaf's URL in a child process, since it is decided at module load.
function resolveEndpoint(env) {
  return JSON.parse(execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const m = await import(" + JSON.stringify(ENDPOINT) + ");"
      + " process.stdout.write(JSON.stringify({ url: m.SERVER_URL, isRemote: m.IS_REMOTE }));"],
    {
      env: { ...process.env, AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "", ...env },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
    },
  ));
}

test("the duplicate derivation is GONE — exactly one module owns the server URL", () => {
  // The whole point. A second derivation is how the precedence and coercion came to disagree in the first
  // place, and nothing would have reported the split.
  assert.doesNotMatch(SERVER, /^\s*(?:const|let|var)\s+__serverUrl\b/m, "__serverUrl must be deleted");
  assert.doesNotMatch(SERVER, /(?<![\w.])__serverUrl\s*[),;]/, "no remaining USE of it (a comment may name it)");
  // SCOPED TO server.js, not to the whole bridge, and I narrowed this after it failed. Four modules declare
  // their own `SERVER_URL`: the endpoint leaf, plus `claude-channel.js`, `doctor.js` and `notify-check.js`.
  // Those three are standalone CLI entry points rather than importers of server.js, and their derivations are
  // pre-existing — `doctor.js` in particular reads `AIFY_COMMS_URL` first and defaults to `localhost`
  // UNCOERCED, which is its own finding and reported separately. Claiming global uniqueness here would assert
  // something this fix does not deliver.
  const owners = declaringModules("SERVER_URL").map((o) => o.file);
  assert.ok(owners.includes("aify-service-endpoint.mjs"), "the endpoint leaf owns the URL server.js uses");
  assert.ok(!owners.includes("server.js"), "server.js must no longer declare one of its own");
  // What IS in scope: no module server.js imports may re-derive a server URL from the same env vars.
  const importedByServer = bridgeSources().filter(([file]) => SERVER.includes(`from "./${file}"`));
  assert.ok(importedByServer.length >= 10, `the import scan should find server.js's leaves, found ${importedByServer.length}`);
  for (const [file, src] of importedByServer) {
    if (file === "aify-service-endpoint.mjs") continue;
    assert.doesNotMatch(
      src, /(?:const|let|var)\s+\w*[Ss]erver[Uu]rl\w*\s*=[^;]*process\.env\.(?:AIFY|CLAUDE_MCP)_SERVER_URL/,
      `${file} is imported by server.js and must not derive its own server URL`,
    );
  }
});

test("ONE PRECEDENCE: both env vars set differently resolve to the same single answer", () => {
  // `__serverUrl` read AIFY first and the leaf reads CLAUDE_MCP first, so with both set the HTTP client and
  // the heartbeats talked to DIFFERENT servers. With one owner there is one answer, whatever it is.
  const r = resolveEndpoint({
    CLAUDE_MCP_SERVER_URL: "http://127.0.0.1:9001",
    AIFY_SERVER_URL: "http://127.0.0.1:9002",
  });
  assert.equal(r.url, "http://127.0.0.1:9001", "the leaf's precedence is CLAUDE_MCP first, and it is now the only one");
  assert.equal(r.isRemote, true);
});

test("ONE COERCION: localhost becomes 127.0.0.1, on the path the heartbeats now use", () => {
  // The reachable half of the defect. The endpoint leaf coerces; `__serverUrl` did not, so heartbeats went to
  // `localhost` — which resolves to IPv6 ::1 first on Windows, where Docker Desktop's IPv6 forwarding times
  // out silently. Now there is one URL and it is coerced.
  assert.equal(resolveEndpoint({ AIFY_SERVER_URL: "http://localhost:8800" }).url, "http://127.0.0.1:8800");
  assert.equal(resolveEndpoint({ CLAUDE_MCP_SERVER_URL: "http://localhost:8800" }).url, "http://127.0.0.1:8800");
  // Not over-eager: a hostname that merely CONTAINS "localhost" must survive.
  assert.equal(
    resolveEndpoint({ AIFY_SERVER_URL: "http://localhost.example:8800" }).url,
    "http://localhost.example:8800", "only the bare localhost host is coerced",
  );
});

test("NO DEFAULT: with neither env var set the URL is EMPTY and IS_REMOTE is false", () => {
  // The root cause. `__serverUrl` defaulted to the loopback service, which is why `!__serverUrl` could never
  // be true. The surviving owner has no default, so "no server configured" is representable.
  const r = resolveEndpoint({});
  assert.equal(r.url, "", "no configured URL must resolve to empty, not to a guessed default");
  assert.equal(r.isRemote, false);
  assert.equal(resolveEndpoint({ AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "" }).url, "", "empty is not a URL");
});

test("the two DIRECT posters are started only when IS_REMOTE, and take the owned URL", () => {
  // These bypass `httpCall` — they are handed a base URL and fetch it. So the guard has to be at the START,
  // not inside the callback: an empty base would make them throw on every tick instead of not running, since
  // `fetch` rejects a relative URL and the heartbeat wrapper swallows it.
  for (const poster of ["makeDefaultHandlePoster", "makeDefaultTurnBusyPoster"]) {
    const at = SERVER.indexOf(poster + "(");
    assert.ok(at > 0, `${poster} must still be wired up`);
    const call = SERVER.slice(at, SERVER.indexOf(")", at) + 1);
    assert.match(call, /\(SERVER_URL,/, `${poster} must be given the owned URL`);
    assert.doesNotMatch(call, /__serverUrl/, `${poster} must not be given the deleted derivation`);
  }
  // Both start sites gate on IS_REMOTE.
  assert.match(SERVER, /const __stopHandleHeartbeat = IS_REMOTE\s*\n\s*\? startSessionHandleHeartbeat\(/,
    "the session-handle heartbeat must not start in local mode");
  assert.match(SERVER, /const __stopTurnBusyHeartbeat = !IS_REMOTE \? \(\) => \{\} : startTurnBusyHeartbeat\(/,
    "the turn-busy heartbeat must not start in local mode");
});

test("the eight callback guards now ask IS_REMOTE — the question they meant", () => {
  // They read `!__serverUrl` and could not fire. Counted, because "some were converted" is the failure this
  // catches: a leftover would be a guard that still cannot fire, sitting next to seven that can.
  //
  // COUNTED ACROSS THE BRIDGE, not in server.js alone. Two of the eight live inside the claude turn-end
  // detector's block, which moved to its own owner in a later slice — so a server.js-only count went from
  // eight to six and failed against correct code. That is the ninth time in this decomposition an assertion
  // of mine measured where code lives; the property is that all eight ask the right question, wherever they
  // are.
  let converted = 0;
  for (const [, src] of bridgeSources()) {
    converted += (src.match(/if \(!(?:AIFY_AGENT_ID|__effectiveAgentId) \|\| !IS_REMOTE\) return;/g) || []).length;
    converted += (src.match(/if \(!IS_REMOTE\) return;/g) || []).length;
    assert.doesNotMatch(src, /!__serverUrl\) return;/, "no guard anywhere may still test the deleted derivation");
  }
  assert.equal(converted, 8, `all eight guards must be converted, found ${converted}`);
});

test("IS_REMOTE's meaning for TOOLS is unchanged", () => {
  // The fix reuses IS_REMOTE as a heartbeat gate. That is only safe because IS_REMOTE already means exactly
  // "a remote service is configured" — the same question every tool asks. Asserted so a future edit that
  // widens IS_REMOTE has to notice it now gates heartbeats too.
  assert.equal(resolveEndpoint({ AIFY_SERVER_URL: "http://127.0.0.1:8800" }).isRemote, true);
  assert.equal(resolveEndpoint({}).isRemote, false);
  assert.equal(resolveEndpoint({ AIFY_SERVER_URL: "not-a-url" }).isRemote, true,
    "IS_REMOTE is truthiness of the configured value, not validity — unchanged, and pinned as such");
});

test("cleanupOnExit still calls the same seven stoppers in the same order", () => {
  // The reviewer parked the teardown-registry reshape, so today's explicit order must survive. The no-op
  // stoppers exist precisely so the non-started case keeps its slot.
  // The regex matches both the `__stopX` handles and the plain `stopX` operations, because the claude
  // detector's stopper became a real exported FUNCTION when its state moved to an owner — the handle no
  // longer leaves that module. Its POSITION is unchanged, which is what this test is for; only the spelling
  // moved, and a test that pinned the spelling would block every future owner move for no benefit.
  const order = [...SERVER.matchAll(/^\s*try \{ (_{0,2}stop\w+)\(\); \}/gm)].map((m) => m[1]);
  assert.deepEqual(order, [
    "__stopHandleHeartbeat",
    "__stopTurnBusyHeartbeat",
    "__stopLivenessHeartbeat",
    "__stopGatewayProbe",
    "__stopResidentHermesTurnDetector",
    "stopClaudeTurnEndDetector",
    "__stopCodexTurnDetector",
  ], "teardown ORDER must be unchanged — the claude entry is now an operation, in the same slot");
});
