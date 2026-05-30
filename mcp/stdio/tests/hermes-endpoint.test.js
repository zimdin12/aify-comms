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
import { agentEndpoint } from "../hermes-endpoint.js";

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
