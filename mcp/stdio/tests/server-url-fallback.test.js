#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const server = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");
const channel = fs.readFileSync(path.join(__dirname, "..", "claude-channel.js"), "utf8");
const installer = fs.readFileSync(path.join(__dirname, "..", "..", "..", "install.sh"), "utf8");

for (const source of [server, channel]) {
  assert.match(source, /AIFY_SERVER_FALLBACK_URLS/);
  // Fallbacks are LOOPBACK-ONLY by design (a hardcoded LAN/docker fallback once silently
  // failed a local bridge over to a remote host); non-loopback fallbacks are explicit
  // opt-in via AIFY_SERVER_FALLBACK_URLS. Publish scrub (2026-07-02) also removed the
  // personal LAN IP default from install.sh — assert the neutral defaults instead.
  assert.match(source, /http:\/\/127\.0\.0\.1:8800/);
  assert.doesNotMatch(source, /192\.168\.\d+\.\d+["'`]/, "no hardcoded LAN IP fallback literals");
}

assert.match(server, /logTransientOrError/);
assert.match(server, /will retry on next poll/);
assert.match(installer, /AIFY_DEFAULT_SERVER_URL:-http:\/\/127\.0\.0\.1:8800/);

console.log("server-url-fallback.test.js: all assertions passed");
