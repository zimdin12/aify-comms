import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ClaudeAdapter } from "../adapters/claude.js";
import { writeClaudeSessionId } from "../claude-session-store.js";

const UUID_A = "aaaaaaaa-1111-2222-3333-444444444444";
const UUID_B = "bbbbbbbb-1111-2222-3333-444444444444";
const UUID_OTHER = "cccccccc-1111-2222-3333-444444444444";

function encodeCwd(cwd) {
  return String(cwd || "").replace(/[^a-zA-Z0-9]/g, "-");
}

function makeHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-home-"));
}

function makeStoreDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-discdir-"));
}

// Write a transcript .jsonl into the cwd-scoped project dir with a controlled mtime.
function writeTranscript(homeDir, cwd, uuid, mtimeMs) {
  const projDir = path.join(homeDir, ".claude", "projects", encodeCwd(cwd));
  fs.mkdirSync(projDir, { recursive: true });
  const file = path.join(projDir, `${uuid}.jsonl`);
  fs.writeFileSync(file, "{}\n");
  if (mtimeMs) fs.utimesSync(file, mtimeMs / 1000, mtimeMs / 1000);
  return file;
}

test("captured store id wins over env and cwd-scoped", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  const cwd = "C:/Users/Administrator/sand_castle";
  try {
    writeTranscript(homeDir, cwd, UUID_B, Date.now());
    writeClaudeSessionId({ sessionId: UUID_A, agentId: "coder-1", dir });
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({
      env: { CLAUDE_SESSION_ID: "env-id-should-lose" },
      homeDir, cwd, agentId: "coder-1", dir,
    });
    assert.strictEqual(got, UUID_A);
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("captured store id is resolved via env.AIFY_AGENT_ID when no agentId opt", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  const cwd = "C:/Users/Administrator/sand_castle";
  try {
    writeTranscript(homeDir, cwd, UUID_B, Date.now());
    writeClaudeSessionId({ sessionId: UUID_A, agentId: "env-agent", dir });
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({
      env: { AIFY_AGENT_ID: "env-agent", CLAUDE_SESSION_ID: "env-id-should-lose" },
      homeDir, cwd, dir,
    });
    assert.strictEqual(got, UUID_A);
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("env CLAUDE_SESSION_ID wins over cwd-scoped when no captured id", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  const cwd = "C:/Users/Administrator/sand_castle";
  try {
    writeTranscript(homeDir, cwd, UUID_B, Date.now());
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({
      env: { CLAUDE_SESSION_ID: "  env-wins-id  " },
      homeDir, cwd, agentId: "no-captured-agent", dir,
    });
    assert.strictEqual(got, "env-wins-id");
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("cross-repo isolation: fresher transcript in a DIFFERENT project dir is NOT returned", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  const myCwd = "C:/Users/Administrator/sand_castle";
  const otherCwd = "C:/Users/Administrator/other_repo";
  try {
    // my own dir: older transcript
    writeTranscript(homeDir, myCwd, UUID_B, Date.now() - 60000);
    // a DIFFERENT project: much fresher transcript (the contamination source)
    writeTranscript(homeDir, otherCwd, UUID_OTHER, Date.now());
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({
      env: {}, homeDir, cwd: myCwd, agentId: "no-captured-agent", dir,
    });
    assert.strictEqual(got, UUID_B, "must return own-dir id, never the fresher foreign one");
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("cwd-scoped returns the newest .jsonl within the OWN project dir", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  const cwd = "C:/Users/Administrator/sand_castle";
  try {
    writeTranscript(homeDir, cwd, UUID_A, Date.now() - 60000);
    writeTranscript(homeDir, cwd, UUID_B, Date.now());
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({ env: {}, homeDir, cwd, agentId: "no-captured-agent", dir });
    assert.strictEqual(got, UUID_B);
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("falls back to AIFY_AGENT_CWD when cwd not passed", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  const cwd = "C:/Users/Administrator/sand_castle";
  try {
    writeTranscript(homeDir, cwd, UUID_A, Date.now());
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({
      env: { AIFY_AGENT_CWD: cwd }, homeDir, agentId: "no-captured-agent", dir,
    });
    assert.strictEqual(got, UUID_A);
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("nothing available -> null", async () => {
  const homeDir = makeHome();
  const dir = makeStoreDir();
  try {
    const a = new ClaudeAdapter();
    const got = await a.discoverSessionId({
      env: {}, homeDir, cwd: "C:/nope/empty", agentId: "no-captured-agent", dir,
    });
    assert.strictEqual(got, null);
  } finally {
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
