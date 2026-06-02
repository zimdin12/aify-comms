#!/usr/bin/env node
// Unit tests for agentEndpoint — the pure-ish id→{host,port,baseUrl,key}
// derivation that gives each hermes agent its OWN api_server endpoint.
//
// port is deterministic (stable hash of agentId, no randomness); key is
// generated once with crypto.randomBytes and persisted to a per-agent key
// file, then read back identically on subsequent calls. Tests inject a temp
// dir and clean it up so nothing touches the real os.tmpdir().

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { agentEndpoint, clearGatewayMarkers } from "../hermes-endpoint.js";

// Make a throwaway temp dir for the per-agent key files.
function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "hermes-endpoint-test-"));
}
function cleanup(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best-effort */
  }
}

test("port is deterministic: same agentId → same port", () => {
  const dir = makeTempDir();
  try {
    const a = agentEndpoint("agent-alpha", { tempDir: dir });
    const b = agentEndpoint("agent-alpha", { tempDir: dir });
    assert.equal(a.port, b.port);
    assert.equal(a.baseUrl, b.baseUrl);
    assert.equal(a.host, "127.0.0.1");
  } finally {
    cleanup(dir);
  }
});

test("port is in the documented range 8642–9641", () => {
  const dir = makeTempDir();
  try {
    for (const id of ["a", "b", "manager", "coder-1", "x".repeat(200), "déjà-vu"]) {
      const ep = agentEndpoint(id, { tempDir: dir });
      assert.ok(ep.port >= 8642 && ep.port <= 9641, `port ${ep.port} out of range for ${id}`);
      assert.equal(ep.baseUrl, `http://127.0.0.1:${ep.port}`);
    }
  } finally {
    cleanup(dir);
  }
});

test("two different agentIds get independent ports (no forced collision)", () => {
  const dir = makeTempDir();
  try {
    const a = agentEndpoint("alpha", { tempDir: dir });
    const b = agentEndpoint("bravo", { tempDir: dir });
    // Not guaranteed unequal in general, but these two specific ids must differ.
    assert.notEqual(a.port, b.port, "alpha and bravo should hash to different ports");
  } finally {
    cleanup(dir);
  }
});

test("key persists across calls: second read equals first", () => {
  const dir = makeTempDir();
  try {
    const a = agentEndpoint("persist-me", { tempDir: dir });
    const b = agentEndpoint("persist-me", { tempDir: dir });
    assert.ok(typeof a.key === "string" && a.key.length > 0, "key must be a non-empty string");
    assert.equal(b.key, a.key, "key must be stable across calls");
  } finally {
    cleanup(dir);
  }
});

test("key file is created under tempDir for the agent", () => {
  const dir = makeTempDir();
  try {
    const ep = agentEndpoint("file-check", { tempDir: dir });
    const files = fs.readdirSync(dir);
    const keyFile = files.find((f) => f.startsWith("aify-hermes-key-"));
    assert.ok(keyFile, `expected a key file under ${dir}, found: ${files.join(", ")}`);
    const onDisk = fs.readFileSync(path.join(dir, keyFile), "utf8").trim();
    assert.equal(onDisk, ep.key, "on-disk key must match returned key");
  } finally {
    cleanup(dir);
  }
});

test("distinct agentIds get distinct key files (one identity per agent)", () => {
  const dir = makeTempDir();
  try {
    const a = agentEndpoint("agent-one", { tempDir: dir });
    const b = agentEndpoint("agent-two", { tempDir: dir });
    assert.notEqual(a.key, b.key, "each agent must get its own key");
    const files = fs.readdirSync(dir).filter((f) => f.startsWith("aify-hermes-key-"));
    assert.equal(files.length, 2, "expected one key file per agent");
  } finally {
    cleanup(dir);
  }
});

// --- clearGatewayMarkers (Task 4.1) ---------------------------------------
// On a TERMINAL teardown (agent removed / explicit stop), the per-agent port
// and key markers must be removed so a restart is a clean slate and a stale
// port marker can never strand a future probe/reuse. Best-effort: missing
// files must not throw. Scoped to ONE agent — sibling agents' markers untouched.

test("clearGatewayMarkers removes the agent's port and key markers", () => {
  const dir = makeTempDir();
  try {
    // Materialize both markers for the agent.
    const ep = agentEndpoint("teardown-me", { tempDir: dir });
    // resolveGatewayPort writes the port file; force it by reading endpoint then
    // writing a port marker the same way the daemon path would.
    fs.writeFileSync(path.join(dir, "aify-hermes-port-teardown-me"), String(ep.port));
    assert.ok(fs.existsSync(path.join(dir, "aify-hermes-key-teardown-me")), "key marker should exist pre-clear");
    assert.ok(fs.existsSync(path.join(dir, "aify-hermes-port-teardown-me")), "port marker should exist pre-clear");

    clearGatewayMarkers("teardown-me", dir);

    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-key-teardown-me")), "key marker should be removed");
    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-port-teardown-me")), "port marker should be removed");
  } finally {
    cleanup(dir);
  }
});

test("clearGatewayMarkers does not throw when markers are missing", () => {
  const dir = makeTempDir();
  try {
    assert.doesNotThrow(() => clearGatewayMarkers("never-existed", dir));
  } finally {
    cleanup(dir);
  }
});

test("clearGatewayMarkers is scoped to one agent (siblings untouched)", () => {
  const dir = makeTempDir();
  try {
    agentEndpoint("keep-me", { tempDir: dir });
    fs.writeFileSync(path.join(dir, "aify-hermes-port-keep-me"), "8888");
    agentEndpoint("drop-me", { tempDir: dir });
    fs.writeFileSync(path.join(dir, "aify-hermes-port-drop-me"), "8889");

    clearGatewayMarkers("drop-me", dir);

    assert.ok(fs.existsSync(path.join(dir, "aify-hermes-key-keep-me")), "sibling key marker must survive");
    assert.ok(fs.existsSync(path.join(dir, "aify-hermes-port-keep-me")), "sibling port marker must survive");
    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-key-drop-me")), "target key marker removed");
    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-port-drop-me")), "target port marker removed");
  } finally {
    cleanup(dir);
  }
});

test("clearGatewayMarkers defaults dir to os.tmpdir() without throwing", () => {
  // No dir arg → must not throw even if nothing to clean.
  assert.doesNotThrow(() => clearGatewayMarkers("no-dir-agent"));
});
