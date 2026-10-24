import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { tmpDir } from "./_tmpdir.js";
import { GatewayMarkerStore, defaultGatewayMarkerDir } from "../hermes-gateway-marker.mjs";
import {
  agentEndpoint, clearGatewayMarkers, readGatewayUrlMarker, writeGatewayUrlMarker,
  readSessionIdMarker, writeSessionIdMarker,
} from "../hermes-endpoint.js";
import { startResumeMarkerSync } from "../hermes-active-session.mjs";
import { startSessionEffort } from "../hermes-session-effort.mjs";
import { readGatewaySessionCount } from "../gateway-orphan-check.mjs";

const gatewayRoot = () => path.join(os.homedir(), ".aify", "hermes", "gateways");
const fixtureUrl = "ws://127.0.0.1:1/api/ws?token=fixture-only";
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("a missing default gateway read creates nothing, and writers publish private complete JSON", () => {
  assert.equal(defaultGatewayMarkerDir(), gatewayRoot());
  const store = new GatewayMarkerStore();
  assert.equal(fs.existsSync(gatewayRoot()), false);
  assert.equal(readGatewayUrlMarker("missing-fixture"), null);
  assert.equal(fs.existsSync(gatewayRoot()), false, "a reader must not create the store");
  assert.equal(writeGatewayUrlMarker("private-fixture", fixtureUrl), true);
  const file = path.join(gatewayRoot(), "aify-hermes-gateway-private-fixture");
  assert.equal(JSON.parse(fs.readFileSync(file, "utf8")).gatewayUrl, fixtureUrl);
  assert.equal(writeGatewayUrlMarker("private-fixture", "wss://fixture.invalid/replaced"), true);
  assert.equal(store.read("private-fixture")?.gatewayUrl, "wss://fixture.invalid/replaced");
  assert.deepEqual(fs.readdirSync(gatewayRoot()), ["aify-hermes-gateway-private-fixture"], "no publication temp file remains");
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(gatewayRoot()).mode & 0o777, 0o700);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  }
});

test("a failed publication leaves the previous complete marker and no temporary file", (t) => {
  const gatewayDir = tmpDir("aify-issue21-publication-");
  assert.equal(writeGatewayUrlMarker("publish-fixture", fixtureUrl, { gatewayDir }), true);
  let publication;
  t.mock.method(fs, "renameSync", (temp, file) => {
    publication = { next: JSON.parse(fs.readFileSync(temp, "utf8")), previous: JSON.parse(fs.readFileSync(file, "utf8")) };
    throw new Error("fixture publication refused");
  });
  assert.equal(writeGatewayUrlMarker("publish-fixture", "wss://new.invalid/ws", { gatewayDir }), false);
  assert.deepEqual(publication, {
    next: { gatewayUrl: "wss://new.invalid/ws", gatewayTokenEnv: "" },
    previous: { gatewayUrl: fixtureUrl, gatewayTokenEnv: "" },
  }, "the real writer must finish private JSON before replacing the intact destination");
  assert.equal(readGatewayUrlMarker("publish-fixture", { gatewayDir })?.gatewayUrl, fixtureUrl);
  assert.deepEqual(fs.readdirSync(gatewayDir), ["aify-hermes-gateway-publish-fixture"]);
});

test("explicit directory overrides do not leak into default discovery", () => {
  const tempDir = tmpDir("aify-issue21-explicit-root-");
  assert.equal(writeGatewayUrlMarker("explicit-fixture", fixtureUrl, { tempDir }), true);
  assert.equal(readGatewayUrlMarker("explicit-fixture", { tempDir })?.gatewayUrl, fixtureUrl);
  assert.equal(readGatewayUrlMarker("explicit-fixture"), null);
});

test("a stable marker beats a different legacy TEMP marker", () => {
  const id = "precedence-fixture";
  assert.equal(writeGatewayUrlMarker(id, fixtureUrl), true);
  fs.writeFileSync(path.join(process.env.TEMP, `aify-hermes-gateway-${id}`),
    JSON.stringify({ gatewayUrl: "wss://legacy.invalid/ws", gatewayTokenEnv: "LEGACY_TOKEN" }));
  assert.deepEqual(readGatewayUrlMarker(id), { gatewayUrl: fixtureUrl, gatewayTokenEnv: "" });
});

test("a legacy-only marker is read without creating a stable copy or rewriting legacy bytes", () => {
  const id = "legacy-only-fixture";
  const file = path.join(process.env.TEMP, `aify-hermes-gateway-${id}`);
  const payload = { gatewayUrl: fixtureUrl, gatewayTokenEnv: "LEGACY_FIXTURE_TOKEN" };
  const bytes = JSON.stringify(payload);
  fs.writeFileSync(file, bytes);
  const stableFile = path.join(gatewayRoot(), `aify-hermes-gateway-${id}`);
  const stableDirExisted = fs.existsSync(gatewayRoot());
  assert.equal(fs.existsSync(file), true);
  assert.equal(fs.existsSync(stableFile), false);
  assert.deepEqual(readGatewayUrlMarker(id), payload);
  assert.equal(fs.readFileSync(file, "utf8"), bytes);
  assert.equal(fs.existsSync(stableFile), false, "fallback does not migrate or publish");
  assert.equal(fs.existsSync(gatewayRoot()), stableDirExisted, "fallback creates no store");
  assert.equal(readGatewayUrlMarker("neither-location-fixture"), null, "the same reader can refuse absence");
});

test("split-root cleanup removes target stable URL and legacy launch files, never persistent session or siblings", () => {
  const tempDir = process.env.TEMP;
  for (const id of ["drop-fixture", "keep-fixture"]) {
    assert.equal(writeGatewayUrlMarker(id, fixtureUrl), true);
    agentEndpoint(id, { tempDir });
    fs.writeFileSync(path.join(tempDir, `aify-hermes-port-${id}`), "9147");
    assert.equal(writeSessionIdMarker(id, "durable_fixture", { tempDir }), true);
    assert.equal(writeGatewayUrlMarker(id, "ws://legacy.invalid/ws", { tempDir }), true);
  }
  clearGatewayMarkers("drop-fixture", tempDir);
  assert.equal(readGatewayUrlMarker("drop-fixture"), null);
  assert.equal(readGatewayUrlMarker("keep-fixture")?.gatewayUrl, fixtureUrl);
  for (const kind of ["port", "key", "gateway"]) {
    assert.equal(fs.existsSync(path.join(tempDir, `aify-hermes-${kind}-drop-fixture`)), false);
    assert.equal(fs.existsSync(path.join(tempDir, `aify-hermes-${kind}-keep-fixture`)), true);
  }
  for (const id of ["drop-fixture", "keep-fixture"]) assert.equal(readSessionIdMarker(id, { tempDir }), "durable_fixture");
});

test("resume sync and effort read actual default gateway markers while their sessions stay in TEMP", async () => {
  const tempDir = tmpDir("aify-issue21-session-");
  assert.equal(writeGatewayUrlMarker("sync-fixture", fixtureUrl), true);
  assert.equal(writeSessionIdMarker("sync-fixture", "old_fixture", { tempDir }), true);
  const opened = [], patches = [], sets = [];
  const list = { sessions: [
    { id: "ephemeral_fixture", session_key: "durable_fixture", started_at: "2026-01-01T00:00:00Z" },
    { id: "newer_fixture", session_key: "durable_newer", started_at: "2026-02-01T00:00:00Z" },
  ] };
  const openWs = async (url) => {
    opened.push(url);
    return { request: async (frame) => {
      if (frame.method === "config.set") sets.push(frame.params);
      return list;
    }, close() {} };
  };
  const stopSync = startResumeMarkerSync({ agentId: "sync-fixture", tempDir, intervalMs: 100000,
    openWs, httpCall: async (...args) => patches.push(args) });
  try { await flush(); } finally { stopSync(); }
  assert.deepEqual(opened, [fixtureUrl]);
  assert.equal(readSessionIdMarker("sync-fixture", { tempDir }), "durable_newer");
  assert.equal(readSessionIdMarker("sync-fixture", { tempDir: gatewayRoot() }), "");
  assert.equal(patches[0]?.[2]?.sessionHandle, "durable_newer");
  // Pin an older live session in the actual session marker, not an injected read stub.
  assert.equal(writeSessionIdMarker("sync-fixture", "ephemeral_fixture", { tempDir }), true);
  const stopEffort = startSessionEffort({ agentId: "sync-fixture", effort: "high", tempDir,
    intervalMs: 100000, openWs, log: () => {} });
  try { await flush(); } finally { stopEffort(); }
  assert.deepEqual(opened, [fixtureUrl, fixtureUrl]);
  assert.deepEqual(sets, [{ key: "reasoning", value: "high", session_id: "ephemeral_fixture" }]);
});

test("gateway roots can be injected separately from session roots", async () => {
  const gatewayDir = tmpDir("aify-issue21-gateway-override-");
  const tempDir = tmpDir("aify-issue21-session-override-");
  assert.equal(writeGatewayUrlMarker("override-fixture", fixtureUrl, { gatewayDir }), true);
  const opened = [];
  const openWs = async (url) => { opened.push(url); return {
    request: async () => ({ sessions: [{ id: "injected_fixture" }] }), close() {},
  }; };
  const stopSync = startResumeMarkerSync({ agentId: "override-fixture", tempDir, gatewayDir,
    intervalMs: 100000, openWs, httpCall: async () => {} });
  try { await flush(); } finally { stopSync(); }
  const stopEffort = startSessionEffort({ agentId: "override-fixture", tempDir, gatewayDir,
    intervalMs: 100000, effort: "high", openWs, log: () => {} });
  try { await flush(); } finally { stopEffort(); }
  assert.deepEqual(opened, [fixtureUrl, fixtureUrl]);
  assert.equal(readSessionIdMarker("override-fixture", { tempDir }), "injected_fixture");
  assert.equal(readGatewayUrlMarker("override-fixture"), null);
  clearGatewayMarkers("override-fixture", tempDir, { gatewayDir });
  assert.equal(readGatewayUrlMarker("override-fixture", { gatewayDir }), null);
});

test("doctor reads the production default writer and preserves unknown instead of zero", async () => {
  assert.equal(writeGatewayUrlMarker("doctor-fixture", fixtureUrl), true);
  let closes = 0;
  const open = async (url) => {
    assert.equal(url, fixtureUrl);
    return { request: async () => ({ sessions: [{ id: "doctor_fixture" }] }), close() { closes++; } };
  };
  assert.equal(await readGatewaySessionCount("doctor-fixture", { open }), 1);
  assert.equal(closes, 1);
  assert.equal(await readGatewaySessionCount("missing-doctor-fixture", { open: () => { throw new Error("must not connect without marker"); } }), null);
  assert.equal(await readGatewaySessionCount("doctor-fixture", {
    open: async () => ({ request: async () => ({ unexpected: true }), close() {} }),
  }), null);
});
