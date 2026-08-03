import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { handleClaudeSessionHook } from "../claude-session-hook.js";
import { readClaudeSessionId } from "../claude-session-store.js";
import { tmpDir } from "./_tmpdir.js";

const HOOK = fileURLToPath(new URL("../claude-session-hook.js", import.meta.url));

test("handler writes the session_id keyed by AIFY_AGENT_ID", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    const payload = JSON.stringify({ session_id: "hook-sid-123", cwd: "C:/x" });
    handleClaudeSessionHook({ stdin: payload, env: { AIFY_AGENT_ID: "coder-1" }, dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "coder-1", dir }), "hook-sid-123");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("handler accepts AIFY_COMMS_AGENT_ID as a fallback", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    const payload = JSON.stringify({ session_id: "hook-sid-fb" });
    handleClaudeSessionHook({ stdin: payload, env: { AIFY_COMMS_AGENT_ID: "coder-fb" }, dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "coder-fb", dir }), "hook-sid-fb");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("handler ignores empty session_id (no file written)", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    handleClaudeSessionHook({ stdin: JSON.stringify({ session_id: "" }), env: { AIFY_AGENT_ID: "coder-2" }, dir });
    assert.strictEqual(readClaudeSessionId({ agentId: "coder-2", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("handler ignores missing agentId (no file written)", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    handleClaudeSessionHook({ stdin: JSON.stringify({ session_id: "has-sid" }), env: {}, dir });
    // nothing was keyed, so nothing can be read back for any agent id
    assert.strictEqual(readClaudeSessionId({ agentId: "has-sid", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("handler tolerates garbage stdin without throwing", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    assert.doesNotThrow(() => handleClaudeSessionHook({ stdin: "not json at all", env: { AIFY_AGENT_ID: "coder-3" }, dir }));
    assert.strictEqual(readClaudeSessionId({ agentId: "coder-3", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("spawning the hook with piped stdin writes the store file and exits 0", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    const res = spawnSync(process.execPath, [HOOK], {
      input: JSON.stringify({ session_id: "spawned-sid-999" }),
      env: { ...process.env, TEMP: dir, TMP: dir, AIFY_AGENT_ID: "spawned-agent" },
      encoding: "utf-8",
    });
    assert.strictEqual(res.status, 0, "hook must exit 0");
    assert.strictEqual(readClaudeSessionId({ agentId: "spawned-agent", dir }), "spawned-sid-999");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("spawning the hook with empty stdin exits 0 and writes nothing", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    const res = spawnSync(process.execPath, [HOOK], {
      input: "",
      env: { ...process.env, TEMP: dir, TMP: dir, AIFY_AGENT_ID: "spawned-agent" },
      encoding: "utf-8",
    });
    assert.strictEqual(res.status, 0, "hook must exit 0 on empty stdin");
    assert.strictEqual(readClaudeSessionId({ agentId: "spawned-agent", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("spawning the hook without an agentId exits 0 and writes nothing", () => {
  const dir = tmpDir("aify-claude-hook-");
  try {
    const env = { ...process.env, TEMP: dir, TMP: dir };
    delete env.AIFY_AGENT_ID;
    delete env.AIFY_COMMS_AGENT_ID;
    const res = spawnSync(process.execPath, [HOOK], {
      input: JSON.stringify({ session_id: "spawned-sid-noid" }),
      env,
      encoding: "utf-8",
    });
    assert.strictEqual(res.status, 0, "hook must exit 0 with no agent id");
    assert.strictEqual(readClaudeSessionId({ agentId: "spawned-sid-noid", dir }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
