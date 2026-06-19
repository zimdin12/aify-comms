#!/usr/bin/env node
// End-to-end test for claude-stop-gate.js (SECONDARY). Spawns the REAL gate process against a
// stub turn-end server, exercising the full path: stdin parse → transcript tail read → classify
// → suppress-or-POST. This is the coverage the pure-decision test can't give: it proves the
// load-bearing FAIL-SAFE claim — on ended/missing/error the gate POSTs /turn-end; only a
// confirmed in-flight tail suppresses (so it can never cause a stuck-`working`).
//
// Run: node --test mcp/stdio/claude-stop-gate.e2e.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const GATE = join(dirname(fileURLToPath(import.meta.url)), "claude-stop-gate.js");
const jl = (obj) => JSON.stringify(obj) + "\n";
const ENDED = jl({ type: "assistant", message: { role: "assistant", stop_reason: "end_turn", content: [{ type: "text", text: "done" }] } });
const INFLIGHT = jl({ type: "assistant", message: { role: "assistant", stop_reason: "tool_use", content: [{ type: "tool_use", id: "t1", name: "Bash" }] } });

// Spawn the gate with a stub /turn-end server; resolve whether a POST arrived.
function runGate({ transcript, badPath = false, noPath = false }) {
  return new Promise((resolve) => {
    let posted = false;
    const server = createServer((req, res) => {
      if (req.method === "POST" && /\/turn-end$/.test(req.url)) posted = true;
      res.end("{}");
    });
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      let transcriptPath = "";
      if (noPath) {
        transcriptPath = undefined;
      } else if (badPath) {
        transcriptPath = join(tmpdir(), "aify-gate-nonexistent-" + port + ".jsonl");
      } else {
        const dir = mkdtempSync(join(tmpdir(), "aify-gate-"));
        transcriptPath = join(dir, "transcript.jsonl");
        writeFileSync(transcriptPath, transcript);
      }
      const child = spawn(process.execPath, [GATE], {
        env: { ...process.env, AIFY_AGENT_ID: "gate-test", AIFY_COMMS_URL: `http://127.0.0.1:${port}` },
        stdio: ["pipe", "ignore", "ignore"],
      });
      child.stdin.end(JSON.stringify(noPath ? {} : { transcript_path: transcriptPath }));
      child.on("exit", () => {
        // The gate awaits its POST before exit, so by exit the stub has been hit (or not).
        setTimeout(() => { server.close(); resolve(posted); }, 50);
      });
    });
  });
}

test("e2e: ended transcript → gate POSTs /turn-end", async () => {
  assert.equal(await runGate({ transcript: ENDED }), true);
});

test("e2e: in-flight transcript → gate SUPPRESSES (no POST)", async () => {
  assert.equal(await runGate({ transcript: INFLIGHT }), false);
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
