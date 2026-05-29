import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  claudeSessionStorePath,
  writeClaudeSessionId,
  readClaudeSessionId,
} from "../claude-session-store.js";

test("claudeSessionStorePath uses agentId-keyed filename in the given dir", () => {
  assert.strictEqual(
    claudeSessionStorePath("coder-1", "/tmp/x"),
    path.join("/tmp/x", "aify-claude-session-coder-1.json"),
  );
});

test("claudeSessionStorePath sanitizes unsafe agentId chars", () => {
  assert.strictEqual(
    claudeSessionStorePath("team/coder:01 beta", "/tmp/x"),
    path.join("/tmp/x", "aify-claude-session-team_coder_01_beta.json"),
  );
});

test("store write -> read round trip returns the session id", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    writeClaudeSessionId({ sessionId: "651b895f-aaaa", agentId: "coder-1", dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "coder-1", dir }), "651b895f-aaaa");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("store write trims the session id on read", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    writeClaudeSessionId({ sessionId: "  padded-id  ", agentId: "coder-2", dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "coder-2", dir }), "padded-id");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("blank agentId is a no-op on write and null on read", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    assert.strictEqual(writeClaudeSessionId({ sessionId: "id-x", agentId: "", dir }), false);
    assert.strictEqual(writeClaudeSessionId({ sessionId: "id-x", agentId: "   ", dir }), false);
    assert.strictEqual(writeClaudeSessionId({ sessionId: "id-x", dir }), false);
    assert.strictEqual(readClaudeSessionId({ agentId: "", dir }), null);
    assert.strictEqual(readClaudeSessionId({ agentId: "   ", dir }), null);
    assert.strictEqual(readClaudeSessionId({ dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("two agents keyed separately do not collide (isolation)", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    writeClaudeSessionId({ sessionId: "sid-A", agentId: "agent-a", dir });
    writeClaudeSessionId({ sessionId: "sid-B", agentId: "agent-b", dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "agent-a", dir }), "sid-A");
    assert.strictEqual(readClaudeSessionId({ agentId: "agent-b", dir }), "sid-B");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("missing store file -> null", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    assert.strictEqual(readClaudeSessionId({ agentId: "no-such-agent", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("malformed json -> null", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    fs.writeFileSync(claudeSessionStorePath("bad-json-agent", dir), "{not valid json");
    assert.strictEqual(readClaudeSessionId({ agentId: "bad-json-agent", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("empty session id in store -> null", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  try {
    fs.writeFileSync(
      claudeSessionStorePath("empty-sid-agent", dir),
      JSON.stringify({ sessionId: "   ", agentId: "empty-sid-agent" }),
    );
    assert.strictEqual(readClaudeSessionId({ agentId: "empty-sid-agent", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("store creates its dir if missing", () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-store-"));
  const dir = path.join(base, "nested", "deeper");
  try {
    writeClaudeSessionId({ sessionId: "made-the-dir", agentId: "dir-maker", dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "dir-maker", dir }), "made-the-dir");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});
