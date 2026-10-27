#!/usr/bin/env node
// What claude-aify hands Claude for one launch (comms-http-entry.mjs, 0.9.11 slice 4): the service's /mcp/http,
// with the launch's agent in the identity header the service reads, and the key. The real CLI is run in a sealed
// HOME with an exported key, so it can reach no registry and no service.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { commsHttpEntry } from "../comms-http-entry.mjs";
import { sealedChildEnv } from "./_child-env.mjs";
import { tmpDir } from "./_tmpdir.js";

const CLI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "comms-http-entry.mjs");

function run(...args) {
  const home = tmpDir("aify-entry-home-");
  return spawnSync(process.execPath, [CLI, ...args], {
    env: sealedChildEnv({ HOME: home, USERPROFILE: home, AIFY_HOME: path.join(home, ".aify-comms"), AIFY_API_KEY: "fixture-key" }),
    encoding: "utf8", cwd: os.tmpdir(),
  });
}

test("the entry is the service's /mcp/http, named as the launch's agent, with the key", () => {
  assert.deepEqual(commsHttpEntry({ serviceName: "aify-comms", endpoint: "http://127.0.0.1:8800/", agentId: "a1", apiKey: "k" }), {
    mcpServers: { "aify-comms": { type: "http", url: "http://127.0.0.1:8800/mcp/http", headers: { "X-Aify-Agent-Id": "a1", "X-API-Key": "k" } } },
  });
});

test("the CLI prints that document with the key it resolves; a launch with no agent gets no identity header", () => {
  const named = run("http://127.0.0.1:8800", "aify-comms", "a1");
  assert.equal(named.status, 0, named.stderr);
  assert.deepEqual(JSON.parse(named.stdout).mcpServers["aify-comms"].headers, { "X-Aify-Agent-Id": "a1", "X-API-Key": "fixture-key" });
  const anonymous = run("http://127.0.0.1:8800", "aify-comms");
  assert.deepEqual(JSON.parse(anonymous.stdout).mcpServers["aify-comms"].headers, { "X-API-Key": "fixture-key" });
});

test("without an endpoint and a server name it prints nothing and exits 2", () => {
  const refused = run("http://127.0.0.1:8800");
  assert.deepEqual([refused.status, refused.stdout], [2, ""]);
});
