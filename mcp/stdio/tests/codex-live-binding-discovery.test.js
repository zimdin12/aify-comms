// `discoverCodexLiveBinding` — which live codex app server owns a session, and when to refuse to guess.
//
// The last two entries on the export ratchet's backlog (`runtimes-codex.js` and the `runtimes.js`
// re-export of it). Left until now for a reason worth stating: it reads RUNTIME MARKERS off the
// filesystem, and `listRuntimeMarkers` DELETES any marker whose pid is not alive. An unsealed test of
// this function would have read the operator's live markers on this machine and unlinked the ones
// belonging to processes it could not see — the incident this project already recorded once.
//
// SO `XDG_STATE_HOME` IS SEALED to a temp directory, and the seal is asserted before anything else.
// `markerBaseDir()` derives from it on every call, so the redirection is total: every marker read,
// write and delete below happens under a directory this file created.
//
// WHAT THE FUNCTION IS FOR. A resident codex agent's app server is discovered rather than configured:
// each wrapper writes a marker naming its `ws://` endpoint, and this walks the live ones asking each
// which threads it holds. Getting it wrong binds an agent to ANOTHER agent's app server — the
// cross-contamination class — so the interesting behaviour is where it REFUSES: two servers claiming
// the same session are reported ambiguous with no runtimeConfig at all, rather than one being picked,
// and a supplied handle that no live server holds resolves to NOTHING rather than to a cwd guess.
//
// THE `data`/`threads` SHAPE BUG THIS FILE FOUND. `thread/list` answers under either key depending on
// the app-server build. `pickNewestCodexThreadId` accepted both; the handle-matching read here took
// `listResult.threads` only. Against a `data`-shaped server its thread array was always empty, and
// because every fallback branch is gated on there being NO handle, the whole function returned null
// for EVERY call that supplied one — a resident codex agent with a stored session handle could not be
// bound at all. The tolerance now lives in one accessor, `codexThreadListItems`, used by both.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.resolve(HERE, "fixtures/fake-codex-app-server.mjs");

const STATE_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "aify-markers-"));
process.env.XDG_STATE_HOME = STATE_HOME;
const MARKER_DIR = path.join(STATE_HOME, "aify-comms", "runtime-markers");
fs.mkdirSync(MARKER_DIR, { recursive: true });

const { discoverCodexLiveBinding } = await import("../runtimes-codex.js");
const runtimes = await import("../runtimes.js");

function writeMarker(name, marker) {
  fs.writeFileSync(path.join(MARKER_DIR, `codex-${name}.json`),
    JSON.stringify({ pid: process.pid, runtime: "codex", ...marker }), "utf-8");
}

function clearMarkers() {
  for (const name of fs.readdirSync(MARKER_DIR)) fs.rmSync(path.join(MARKER_DIR, name));
}

function pickPort() {
  return new Promise((resolve, reject) => {
    const s = createServer();
    s.listen(0, "127.0.0.2", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
    s.on("error", reject);
  });
}

async function withFakeAppServers(count, thread, fn) {
  const children = [];
  const urls = [];
  try {
    for (let i = 0; i < count; i += 1) {
      const url = `ws://127.0.0.2:${await pickPort()}`;
      const child = spawn(process.execPath, [FAKE, "--listen", url], {
        cwd: process.cwd(),
        env: { ...process.env, FAKE_CODEX_RESIDENT_THREAD: thread, FAKE_CODEX_THREAD_LIST_KEY: "data" },
        stdio: ["ignore", "pipe", "pipe"],
      });
      await once(child.stdout, "data");
      children.push(child);
      urls.push(url);
    }
    return await fn(urls);
  } finally {
    for (const child of children) {
      child.kill("SIGTERM");
      await once(child, "exit").catch(() => {});
    }
  }
}

// ── the seal ────────────────────────────────────────────────────────────────────────────────────

test("the marker directory is the SEALED one, not the operator's", async () => {
  // Asserted first and by construction: `listRuntimeMarkers` unlinks markers whose pid is not alive,
  // so a test that read the real directory would delete live agents' markers.
  assert.ok(MARKER_DIR.startsWith(STATE_HOME));
  assert.equal(process.env.XDG_STATE_HOME, STATE_HOME);
  const { markerFilePath } = await import("../runtime-markers.js");
  assert.ok(markerFilePath("codex", "/tmp/x").startsWith(MARKER_DIR),
    markerFilePath("codex", "/tmp/x"));
});

test("a marker for a DEAD process is deleted while it is walked", () => {
  // The destructive behaviour the seal protects, asserted inside the sealed directory so the fact is
  // on record rather than discovered by someone testing this without the seal.
  clearMarkers();
  writeMarker("dead", { pid: 999_999_998, appServerUrl: "ws://127.0.0.2:1" });
  assert.equal(fs.readdirSync(MARKER_DIR).length, 1);
  return discoverCodexLiveBinding({}).then(() => {
    assert.deepEqual(fs.readdirSync(MARKER_DIR), []);
  });
});

// ── nothing to discover ─────────────────────────────────────────────────────────────────────────

test("no markers at all is null", async () => {
  clearMarkers();
  assert.equal(await discoverCodexLiveBinding({}), null);
});

test("a marker with NO app server url is not a live binding", async () => {
  // The wrapper writes a marker for every codex session; only the ones running an app server can be
  // driven. Treating the rest as candidates would connect to nothing and report a binding.
  clearMarkers();
  writeMarker("no-url", {});
  assert.equal(await discoverCodexLiveBinding({}), null);
});

test("an app server url that is not a WEBSOCKET is not a live binding", async () => {
  // `http://` is what a half-configured host writes. The transport is a websocket, and a scheme check
  // is the only thing between that and a connection attempt that can never succeed.
  clearMarkers();
  for (const appServerUrl of ["http://127.0.0.2:1", "127.0.0.2:1", ""]) {
    writeMarker("scheme", { appServerUrl });
    assert.equal(await discoverCodexLiveBinding({}), null, appServerUrl);
  }
});

test("a marker whose app server is UNREACHABLE is null, not an error", async () => {
  // Markers outlive the processes that wrote them by design — this is how a stale one is discovered.
  // Throwing here would fail the registration that asked, instead of falling back to message-only.
  clearMarkers();
  writeMarker("gone", { appServerUrl: "ws://127.0.0.2:1" });
  assert.equal(await discoverCodexLiveBinding({}), null);
});

// ── binding to a live server ────────────────────────────────────────────────────────────────────

test("one live app server binds by CWD when no session handle is given", async () => {
  // A fresh registration has no handle yet. The thread whose cwd matches the agent's workspace is the
  // one it is sitting in; picking any other would attach it to somebody else's conversation.
  clearMarkers();
  await withFakeAppServers(1, "thread-cwd", async ([url]) => {
    writeMarker("live", { appServerUrl: url });
    const result = await discoverCodexLiveBinding({ cwd: process.cwd() });
    assert.ok(result, "no binding was discovered");
    assert.equal(result.ambiguous, false);
    assert.equal(result.threadId, "thread-cwd");
    assert.equal(result.runtimeConfig.appServerUrl, url);
  });
});

test("a SESSION HANDLE that one server holds binds to that server", async () => {
  clearMarkers();
  await withFakeAppServers(1, "thread-known", async ([url]) => {
    writeMarker("live", { appServerUrl: url });
    const result = await discoverCodexLiveBinding({
      sessionHandle: "thread-known", cwd: process.cwd(),
    });
    assert.equal(result.threadId, "thread-known");
    assert.equal(result.ambiguous, false);
    assert.equal(result.runtimeConfig.appServerUrl, url);
  });
});

test("the marker's AUTH TOKEN ENV NAME travels into the runtime config", async () => {
  // The token itself is never in the marker — only the name of the variable holding it, which is what
  // keeps a credential out of a world-readable file in the state directory.
  clearMarkers();
  await withFakeAppServers(1, "thread-token", async ([url]) => {
    writeMarker("live", { appServerUrl: url, remoteAuthTokenEnv: "SOME_TOKEN_VAR" });
    const result = await discoverCodexLiveBinding({ cwd: process.cwd() });
    assert.equal(result.runtimeConfig.remoteAuthTokenEnv, "SOME_TOKEN_VAR");
    assert.ok(!JSON.stringify(result).includes("SOME_TOKEN_VALUE"));
  });
});

// ── refusing to guess ───────────────────────────────────────────────────────────────────────────

test("TWO servers holding the same session are AMBIGUOUS, with no config", async () => {
  // The refusal that matters. Two app servers claiming one session id means the bridge cannot tell
  // which process owns the agent, and binding to either would drive somebody else's session. It
  // reports the handle and NO runtimeConfig, so the caller cannot accidentally use half an answer.
  clearMarkers();
  await withFakeAppServers(2, "thread-shared", async ([first, second]) => {
    writeMarker("a", { appServerUrl: first });
    writeMarker("b", { appServerUrl: second });
    const result = await discoverCodexLiveBinding({
      sessionHandle: "thread-shared", cwd: process.cwd(),
    });
    assert.equal(result.ambiguous, true);
    assert.equal(result.threadId, "thread-shared");
    assert.equal(result.runtimeConfig, null);
  });
});

test("a session handle NO server holds is NOT resolved by cwd", async () => {
  // I expected a cwd fallback here and the code refuses — correctly, and the refusal is the point.
  // Every fallback branch is gated on there being NO handle: if one was supplied and no live server
  // holds it, binding to a server that holds a DIFFERENT thread would resume somebody else's
  // conversation. Null means message-only, which is recoverable; the wrong thread is not.
  clearMarkers();
  await withFakeAppServers(1, "thread-actual", async ([url]) => {
    writeMarker("live", { appServerUrl: url });
    const result = await discoverCodexLiveBinding({
      sessionHandle: "thread-that-aged-out", cwd: process.cwd(),
    });
    assert.equal(result, null);
  });
});

test("TWO live servers and no handle is not resolved by cwd alone", async () => {
  // Both match the cwd, so the cwd cannot discriminate. Anything other than a refusal here is a coin
  // toss between two agents' app servers.
  clearMarkers();
  await withFakeAppServers(2, "thread-shared-cwd", async ([first, second]) => {
    writeMarker("a", { appServerUrl: first });
    writeMarker("b", { appServerUrl: second });
    const result = await discoverCodexLiveBinding({ cwd: process.cwd() });
    if (result) {
      assert.equal(result.ambiguous, true, JSON.stringify(result));
      assert.equal(result.runtimeConfig, null);
    }
  });
});

// ── the re-export ───────────────────────────────────────────────────────────────────────────────

test("runtimes.js re-exports the SAME function", () => {
  // Callers reach it through `runtimes.js`. A different function of the same name there would leave
  // the ambiguity refusal above unproven for every real caller.
  assert.equal(runtimes.discoverCodexLiveBinding, discoverCodexLiveBinding);
});

test.after(() => {
  try { fs.rmSync(STATE_HOME, { recursive: true, force: true }); } catch { /* best effort */ }
});
