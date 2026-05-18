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
  assert.match(source, /host\.docker\.internal/);
  assert.match(source, /192\.168\.100\.10/);
}

assert.match(server, /logTransientOrError/);
assert.match(server, /will retry on next poll/);
assert.match(installer, /AIFY_DEFAULT_SERVER_URL:-http:\/\/192\.168\.100\.10:8800/);

console.log("server-url-fallback.test.js: all assertions passed");
