#!/usr/bin/env node
// A managed-dispatch shell may not silently re-register the agent it is running as.
//
// A managed worker is launched to execute one dispatched turn. If the agent inside it calls `comms_register`
// without saying what it means, the registration REPLACES the live agent record — re-registration is a full
// state refresh — and the resident session that actually owns the agent is left described by a short-lived
// worker's environment. So the guard refuses, and says why.
//
// FOR MOST OF ITS LIFE IT ONLY REFUSED AN EXPLICIT `sessionMode: "managed"`. The omitted case — the
// accidental one it was built for — passed straight through. Found by converting this file from a source
// regex to a real handler test, and fixed in the same round; see the note on the omitted-case assertion.
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
import { sealedChildEnv } from "./_child-env.mjs";

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
        ...sealedChildEnv(),
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

// ── THE CASE THE GUARD IS ACTUALLY FOR: an OMITTED sessionMode ───────────────
//
// This is the accidental conversion — an agent inside a managed run calling `comms_register(agentId, role)`
// with no mode at all. It must be refused, and for most of this guard's life it was not.
//
// WHAT WAS WRONG. The condition was `normalizeSessionMode(sessionMode) !== "resident"`, and
// `normalizeSessionMode` fails toward "resident" by design — an unreadable mode must never yield a session
// the bridge may reap. So `normalizeSessionMode(undefined)` was "resident", the condition was false, and the
// managed agent was converted to a resident CLI identity: exactly what the guard exists to prevent and
// exactly what its own error text promises it prevents. Open since `9aebbfcc`, which added the takeover
// hatch inside a previously unconditional guard.
//
// The fix tests EXPLICITNESS instead: `sessionMode !== "resident"` on the raw value. The schema is
// `z.enum(["resident", "managed"]).optional()`, so that comparison is exact rather than lenient.
//
// It was found by converting this file from a source regex to a real handler test — the regex asserted a
// line that was present and correct while the behaviour it implied was absent, which is why the assertion
// below goes through the handler.
const omitted = register({ managed: true, args: { agentId: "mrg-agent", role: "coder" } });
assert.equal(omitted.isError, true,
  "a managed shell registering with NO sessionMode must be refused — this is the accidental conversion");
assert.match(omitted.text, /comms_register without an explicit sessionMode is disabled/,
  "…and the error text finally describes what actually happens");
assert.doesNotMatch(String(omitted.text), /Registered "mrg-agent"/,
  "…and nothing may have been registered");

// ── Outside a managed shell the guard does not apply at all ──────────────────
const ordinary = register({ managed: false, args: { agentId: "mrg-agent", role: "coder" } });
assert.equal(ordinary.isError, false, "an ordinary session registers without naming a sessionMode");
assert.match(ordinary.text, /Registered "mrg-agent"/);

console.log("managed-register-guard.test.js: all assertions passed");
