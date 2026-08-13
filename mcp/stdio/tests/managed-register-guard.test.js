#!/usr/bin/env node
// A managed-dispatch shell may not silently re-register the agent it is running as.
//
// A managed worker is launched to execute one dispatched turn. If the agent inside it calls `comms_register`
// without saying what it means, the registration REPLACES the live agent record — re-registration is a full
// state refresh — and the resident session that actually owns the agent is left described by a short-lived
// worker's environment. So the guard refuses, and says why.
//
// EXCEPT THAT TODAY IT ONLY REFUSES AN EXPLICIT `sessionMode: "managed"`. The omitted case — the accidental
// one the guard was built for — passes through, because `normalizeSessionMode` fails toward "resident". See
// the long note on the last assertion: it is pinned as current behaviour and reported, not fixed here.
//
// THE ESCAPE HATCH IS DELIBERATE AND OPERATOR-VERIFIED (2026-05-22): an explicit `sessionMode: "resident"`
// is an intentional resident-takeover from a managed shell and must pass. A guard that tightened to refuse
// everything would break that on purpose-built workflows, so both directions are pinned.
//
// THIS TEST USED TO BE A REGEX OVER server.js SOURCE. It sliced the `comms_register` body out by searching
// for `server.tool(\n  "comms_register"` — matching the tool's INDENTATION — and asserted the guard's text
// appeared in it. When the tool moved to `registration-tool.mjs` in v0.5.4 and gained one level of nesting,
// the slice found nothing and the file failed with "comms_register tool should exist", despite the guard
// being completely intact. It proved a line was written; it never proved the guard ran, and it could not
// have caught the guard being made unreachable.
//
// It is now the real thing: the real tool, the real handler, a fake service on 127.0.0.2 (never the
// operator's live 127.0.0.1:8800) and a temp local store.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "registration-tool.mjs")).href;

function register({ managed, args }) {
  const store = fs.mkdtempSync(path.join(os.tmpdir(), "aify-mrg-test-"));
  fs.writeFileSync(path.join(store, "agents.json"), '{"agents":{}}');
  const script = `
    import http from "node:http";
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        if (req.method === "POST" && req.url.endsWith("/agents")) {
          res.writeHead(200, { "content-type": "application/json" });
          return res.end(JSON.stringify({
            agentId: "mrg-agent", role: "coder", runtime: "generic",
            sessionMode: "resident", machineId: "test-box", capabilities: {},
          }));
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;
    process.env.CLAUDE_MCP_SERVER_URL = "";
    const { registerRegistrationTool } = await import(${JSON.stringify(LEAF)});
    const { z } = await import("zod");
    const tools = new Map();
    registerRegistrationTool(
      { tool: (name, description, schema, handler) => tools.set(name, { handler }) },
      z,
      { ensureDispatchLoop: () => {} },
    );
    let out = null; let error = null;
    try { out = await tools.get("comms_register").handler(${JSON.stringify(args)}); }
    catch (e) { error = String(e?.message || e); }
    srv.close();
    process.stdout.write(JSON.stringify({
      isError: out?.isError === true,
      text: out?.content?.[0]?.text || null,
      error,
    }));
  `;
  try {
    return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
      env: {
        ...process.env,
        AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "",
        CLAUDE_MCP_MESSAGES_DIR: store,
        AIFY_AGENT_ID: "mrg-agent", AIFY_RUNTIME: "",
        AIFY_MANAGED_DISPATCH: managed ? "1" : "",
      },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
    }));
  } finally {
    fs.rmSync(store, { recursive: true, force: true });
  }
}

// ── The guard REFUSES an explicit managed re-registration, and explains itself ──
const blocked = register({
  managed: true,
  args: { agentId: "mrg-agent", role: "coder", sessionMode: "managed" },
});
assert.equal(blocked.isError, true, "a managed shell re-registering as managed must get an MCP error");
assert.match(blocked.text, /comms_register without an explicit sessionMode is disabled/,
  "the refusal must say why — an unexplained error here reads as a bug in the tool");
assert.equal(blocked.error, null, "the guard returns an error result rather than throwing");

// ── The operator-verified escape hatch passes ────────────────────────────────
const takeover = register({
  managed: true,
  args: { agentId: "mrg-agent", role: "coder", sessionMode: "resident" },
});
assert.equal(takeover.isError, false, "explicit sessionMode='resident' is an intentional takeover and must pass");
assert.match(takeover.text, /Registered "mrg-agent"/, "…and must actually register");

// ── CURRENT BEHAVIOUR, AND IT CONTRADICTS THE GUARD'S OWN STATED PURPOSE ─────
//
// REPORTED, NOT FIXED HERE — changing it is a behaviour change and this landed in a refactor slice whose
// rule is byte-identical bodies. This assertion pins what the code DOES so the contradiction is visible
// and a fix has something to flip, rather than being a silent hole behind a green suite.
//
// OMITTING sessionMode does NOT hit the guard. `normalizeSessionMode` fails toward "resident" by design
// (an unreadable mode must not yield a session the bridge may reap), so `normalizeSessionMode(undefined)`
// is "resident" and the escape-hatch condition `!== "resident"` is false. The managed agent is converted
// to a resident CLI identity — which is the ACCIDENTAL conversion the guard was built to prevent, and the
// case its error message describes ("comms_register without an explicit sessionMode is disabled here").
//
// It has been open since `9aebbfcc`, which added the hatch INSIDE the previously unconditional guard.
// Before that commit a managed shell was refused outright. The test added in that same commit was a regex
// over the guard's source text, so it could not have caught this: the line it asserted is present and
// correct, and the behaviour it implies is not the behaviour.
const omitted = register({ managed: true, args: { agentId: "mrg-agent", role: "coder" } });
assert.equal(omitted.isError, false,
  "CURRENT (contradicts the guard's comment and its error text): an omitted sessionMode is NOT blocked");
assert.match(omitted.text, /Registered "mrg-agent" \(resident/,
  "…and it converts the managed agent to resident, which is what the guard exists to prevent");

// ── Outside a managed shell the guard does not apply at all ──────────────────
const ordinary = register({ managed: false, args: { agentId: "mrg-agent", role: "coder" } });
assert.equal(ordinary.isError, false, "an ordinary session registers without naming a sessionMode");
assert.match(ordinary.text, /Registered "mrg-agent"/);

console.log("managed-register-guard.test.js: all assertions passed");
