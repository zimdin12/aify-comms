#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const server = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");
const channel = fs.readFileSync(path.join(__dirname, "..", "claude-channel.js"), "utf8");
const installer = fs.readFileSync(path.join(__dirname, "..", "..", "..", "install.sh"), "utf8");
// v0.5.4: URL resolution and the failover latch moved OUT of server.js into
// `aify-service-endpoint.mjs` (layer 0 of the server.js decomposition). The loopback-only fallback
// policy these assertions protect did not change — it changed ADDRESS. Checking server.js for it now
// would pass vacuously the day someone deletes the policy, so the check follows the code.
// `claude-channel.js` still carries its own copy in its own process; see the four-way HTTP-caller
// duplication packet.
const endpoint = fs.readFileSync(path.join(__dirname, "..", "aify-service-endpoint.mjs"), "utf8");

for (const source of [endpoint, channel]) {
  assert.match(source, /AIFY_SERVER_FALLBACK_URLS/);
  // Fallbacks are LOOPBACK-ONLY by design (a hardcoded LAN/docker fallback once silently
  // failed a local bridge over to a remote host); non-loopback fallbacks are explicit
  // opt-in via AIFY_SERVER_FALLBACK_URLS. Publish scrub (2026-07-02) also removed the
  // personal LAN IP default from install.sh — assert the neutral defaults instead.
  assert.match(source, /http:\/\/127\.0\.0\.1:8800/);
  assert.doesNotMatch(source, /192\.168\.\d+\.\d+["'`]/, "no hardcoded LAN IP fallback literals");
}

// These stay in server.js: they are claim/spawn bookkeeping that READS the latch through the
// `activeServerUrl()` accessor rather than owning it.
assert.match(server, /logTransientOrError/);
assert.match(server, /noteControlClaimFailure/);
assert.match(server, /noteControlClaimSuccess/);
assert.doesNotMatch(server, /will retry on next poll/);
assert.match(installer, /AIFY_DEFAULT_SERVER_URL:-http:\/\/127\.0\.0\.1:8800/);

console.log("server-url-fallback.test.js: all assertions passed");
