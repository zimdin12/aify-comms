#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const serverText = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");

const registerStart = serverText.indexOf('server.tool(\n  "comms_register"');
assert.ok(registerStart >= 0, "comms_register tool should exist");

const registerBody = serverText.slice(registerStart, serverText.indexOf("const resolvedRuntime", registerStart));
assert.match(registerBody, /if \(IS_MANAGED_DISPATCH\)/, "managed dispatch register guard should run before mutation");
assert.match(registerBody, /comms_register without an explicit sessionMode is disabled/, "guard should explain why registration is blocked");
assert.match(registerBody, /isError:\s*true/, "guard should return an MCP error");
// Operator-verified 2026-05-22: explicit `sessionMode: "resident"` is an
// intentional resident-takeover from a managed-dispatch shell and must
// be allowed through the guard. Pin the escape hatch so the guard
// doesn't quietly tighten again.
assert.match(registerBody, /normalizeSessionMode\(sessionMode\) !== "resident"/, "guard must allow explicit sessionMode='resident' through");

console.log("managed-register-guard.test.js: all assertions passed");
