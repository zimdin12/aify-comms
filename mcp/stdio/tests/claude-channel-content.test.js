#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { writeAgentBindingFile } from "../binding-file.js";
import { tmpDir } from "./_tmpdir.js";

test("Claude channel dispatch content starts with a native aify-comms receipt marker", async () => {
  const { dispatchContent } = await import("../claude-channel.js");
  const text = dispatchContent("claude-test", {
    from: "sender",
    subject: "Hello",
    body: "Payload",
    priority: "normal",
    messageId: "msg-1",
  });

  assert.match(text, /^aify-comms message received\n/);
  assert.match(text, /\[NORMAL\] sender → claude-test: Hello/);
  assert.doesNotMatch(text, /^\+-+\+$/m, "Claude should keep its own channel shape, not Hermes box styling");
});

test("Claude channel require_reply dispatch instructs a same-turn reply (no deferred reply)", async () => {
  const { dispatchContent } = await import("../claude-channel.js");
  const text = dispatchContent("claude-test", {
    from: "sender",
    subject: "Please review",
    body: "Payload",
    priority: "normal",
    messageId: "msg-1",
    requireReply: true,
  });

  // The wake text MUST tell the agent to send its inReplyTo reply in THIS turn,
  // before ending — a managed session is not re-woken to finish a deferred reply
  // (root-caused 2026-06-02: split read+reply across two turns stranded the reply).
  // 14aa4fc (2026-06-18, context-burn trim) shortened the replyLine to
  // "Reply THIS turn before you end: ... A deferred reply strands — the session
  // is not re-woken for it." — same contract, terser wording; the full rationale
  // lives once in the MCP server instructions. Pin the contract, not the prose.
  assert.match(text, /this turn/i, "must direct a same-turn reply");
  assert.match(text, /not re-woken|not be re-woken|won't be re-woken|will not be re-woken/i, "must warn the session is not re-woken to finish a deferred reply");
  assert.match(text, /inReplyTo="msg-1"/, "must reference the inReplyTo handle");
});

test("Claude channel non-require_reply dispatch does NOT force a same-turn reply directive", async () => {
  const { dispatchContent } = await import("../claude-channel.js");
  const text = dispatchContent("claude-test", {
    from: "sender",
    subject: "FYI",
    body: "Payload",
    priority: "normal",
    messageId: "msg-2",
    requireReply: false,
  });
  assert.doesNotMatch(text, /re-woken/i,
    "delivery-only dispatch should not carry the same-turn-reply warning");
});

test("a stopped resident can recover delivery without restarting Claude", { timeout: 10_000 }, async (t) => {
  const stoppedTmp = tmpDir("aify-stopped-channel-");
  writeAgentBindingFile({ pid: process.pid, agentId: "stopped-channel-test", dir: stoppedTmp });

  let claims = 0;
  let resolveSecondClaim;
  const secondClaim = new Promise((resolve) => { resolveSecondClaim = resolve; });
  const api = createServer((req, res) => {
    req.resume();
    res.setHeader("content-type", "application/json");
    if (req.url === "/api/v1/dispatch/claim") {
      claims += 1;
      res.end(JSON.stringify(claims === 1 ? { stopped: true } : {}));
      if (claims === 2) resolveSecondClaim();
      return;
    }
    res.end(JSON.stringify({ ok: true }));
  });
  await new Promise((resolve) => api.listen(0, "127.0.0.2", resolve));

  const child = spawn(process.execPath, [fileURLToPath(new URL("../claude-channel.js", import.meta.url))], {
    env: {
      ...process.env,
      TMP: stoppedTmp,
      TEMP: stoppedTmp,
      AIFY_SERVER_URL: `http://127.0.0.2:${api.address().port}`,
      CLAUDE_MCP_SERVER_URL: `http://127.0.0.2:${api.address().port}`,
      AIFY_COMMS_CHANNEL_POLL_MS: "10",
      AIFY_CHANNEL_RELEASE_RECHECK_MS: "5000",
      AIFY_CHANNEL_PARENT_GUARD_MS: "60000",
      AIFY_CHANNEL_LIVENESS_MS: "60000",
    },
    stdio: ["pipe", "ignore", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  t.after(() => {
    child.kill("SIGTERM");
    api.close();
    rmSync(stoppedTmp, { recursive: true, force: true });
  });

  await Promise.race([
    secondClaim,
    new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error(`channel never reclaimed after stopped; stderr=${stderr}`)), 8000);
      timer.unref();
    }),
  ]);
  assert.equal(claims, 2);
  assert.equal(child.exitCode, null, "the same sidecar process must survive the reversible stop");
});
