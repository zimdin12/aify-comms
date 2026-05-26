#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const serverText = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");

const registerStart = serverText.indexOf('server.tool(\n  "comms_register"');
assert.ok(registerStart >= 0, "comms_register tool should exist");
const registerBody = serverText.slice(registerStart, serverText.indexOf("const capabilities =", registerStart));

assert.ok(
  registerBody.includes("const hermesGatewayRegistration =") &&
    registerBody.includes('resolvedRuntime === "hermes"') &&
    registerBody.includes("runtimeConfig?.gatewayUrl"),
  "Hermes live-gateway registration should be detected before choosing a session handle",
);
assert.match(
  registerBody,
  /const allowPreviousSessionHandle =\s*!\(hermesGatewayRegistration && !String\(sessionHandle \|\| ""\)\.trim\(\)\)/s,
  "Fresh Hermes live-gateway registration without an explicit handle must not reuse previousInfo.sessionHandle",
);
assert.match(
  registerBody,
  /\(allowPreviousSessionHandle \? previousInfo\?\.sessionHandle : ""\)/,
  "Previous handle reuse should be gated by allowPreviousSessionHandle",
);

console.log("hermes-register-fresh-handle.test.js: all assertions passed");
