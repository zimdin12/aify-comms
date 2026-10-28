#!/usr/bin/env node
// End-to-end test for claude-stop-gate.js (SECONDARY). Spawns the REAL gate process against a
// stub aify-env turn-event receiver, exercising the full path: stdin parse → transcript tail read →
// classify → suppress-or-POST. This is the coverage the pure-decision test can't give: it proves the
// load-bearing FAIL-SAFE claim — on ended/missing/error the gate POSTs its turn-end; only a
// confirmed in-flight tail suppresses (so it can never cause a stuck-`working`).
//
// Since 0.9.9 S2 the turn-end goes to aify-env only (agent-state-event.mjs), through the descriptor
// `~/.aify/env/<instance>.json` names, so the stub is that descriptor's endpoint in a private home.

import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { sealedChildEnv } from "./_child-env.mjs";

// `..`, because this test moved into `tests/` in 2026-08-16. It and its unit sibling sat at the
// TOP LEVEL of mcp/stdio as `*.test.mjs`, where `run-all.mjs` — which reads `tests/` for
// `*.test.js` — could not see them, so neither had ever run in a suite. Both passed on their
// first execution; the coverage gate had been crediting `claude-stop-gate.js` as tested on the
// strength of files nothing executed.
const GATE = join(dirname(fileURLToPath(import.meta.url)), "..", "claude-stop-gate.js");
const LIFETIME = "7f3c9e2a-2222-4222-8222-222222222222";
const jl = (obj) => JSON.stringify(obj) + "\n";
const ENDED = jl({ type: "assistant", message: { role: "assistant", stop_reason: "end_turn", content: [{ type: "text", text: "done" }] } });
const INFLIGHT = jl({ type: "assistant", message: { role: "assistant", stop_reason: "tool_use", content: [{ type: "tool_use", id: "t1", name: "Bash" }] } });

// Spawn the gate with a stub turn-event receiver; resolve whether a turn-end arrived.
function runGate({ transcript, badPath = false, noPath = false, payload = {}, onPost = () => {} }) {
  return new Promise((resolve) => {
    let posted = false;
    const server = createServer((req, res) => {
      let text = "";
      req.on("data", (c) => { text += c; });
      req.on("end", () => {
        if (req.method === "POST" && req.url === "/agents/gate-test/turn-event" && JSON.parse(text).kind === "turn-end") {
          posted = true;
          onPost(req, JSON.parse(text));
        }
        res.end("{}");
      });
    });
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      const home = mkdtempSync(join(tmpdir(), "aify-gate-home-"));
      mkdirSync(join(home, ".aify", "env"), { recursive: true });
      writeFileSync(join(home, ".aify", "env", "test.json"), JSON.stringify({
        url: `http://127.0.0.1:${port}`, instance: "test", pid: process.pid, startedAt: "2026-10-01T00:00:00.000Z",
      }));
      let transcriptPath = "";
      if (noPath) {
        transcriptPath = undefined;
      } else if (badPath) {
        transcriptPath = join(home, "aify-gate-nonexistent.jsonl");
      } else {
        transcriptPath = join(home, "transcript.jsonl");
        writeFileSync(transcriptPath, transcript);
      }
      const child = spawn(process.execPath, [GATE], {
        env: { ...sealedChildEnv(), HOME: home, USERPROFILE: home, AIFY_AGENT_ID: "gate-test",
          AIFY_ENV_INSTANCE: "test", AIFY_LIFETIME: LIFETIME, AIFY_API_KEY: "gate-key" },
        stdio: ["pipe", "ignore", "ignore"],
      });
      child.stdin.end(JSON.stringify(noPath ? payload : { ...payload, transcript_path: transcriptPath }));
      child.on("exit", () => {
        // The gate awaits its POST before exit, so by exit the stub has been hit (or not).
        setTimeout(() => { server.close(); rmSync(home, { recursive: true, force: true }); resolve(posted); }, 50);
      });
    });
  });
}

test("e2e: ended transcript → gate POSTs its turn-end", async () => {
  assert.equal(await runGate({ transcript: ENDED }), true);
});

test("e2e: in-flight transcript → gate SUPPRESSES (no POST)", async () => {
  assert.equal(await runGate({ transcript: INFLIGHT }), false);
});

test("e2e: a Stop that names its final message ends the turn though the tail is not yet flushed", async () => {
  // 2026-10-28, measured: Claude ran the Stop hook before writing the turn's final line, so the tail ended in the
  // tool_result and the gate held comms-tech-lead and env-senior-dev at `working` after their turns ended.
  const unflushed = INFLIGHT + jl({ type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "ok" }] } });
  assert.equal(await runGate({ transcript: unflushed }), false, "control: this tail alone reads in-flight");
  assert.equal(await runGate({ transcript: unflushed, payload: { last_assistant_message: "   " } }), false, "a blank message names nothing");
  assert.equal(await runGate({ transcript: unflushed, payload: { last_assistant_message: "Done." } }), true);
});

test("e2e: FAIL-SAFE — missing transcript file → gate POSTs (never sticks)", async () => {
  assert.equal(await runGate({ badPath: true }), true);
});

test("e2e: FAIL-SAFE — no transcript_path in payload → gate POSTs", async () => {
  assert.equal(await runGate({ noPath: true }), true);
});

test("e2e: FAIL-SAFE — empty/garbage transcript → unknown → gate POSTs", async () => {
  assert.equal(await runGate({ transcript: "not json\n{partial\n" }), true);
});

test("e2e: the gate's turn-end names its lifetime and carries no credential to aify-env", async () => {
  let key = "unset";
  let body;
  assert.equal(await runGate({ transcript: ENDED, onPost: (req, sent) => { key = req.headers["x-api-key"]; body = sent; } }), true);
  assert.equal(key, undefined, "the comms key must not reach aify-env");
  assert.equal(body.lifetime, LIFETIME);
  assert.equal(body.instance, "test");
});
