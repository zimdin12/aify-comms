#!/usr/bin/env node
// The installer's registry write, exercised as the installer runs it.
//
// The pure upsert is covered next door. What is only reachable here is the wiring: that the CLI reads
// an absent file as "first service on this host" rather than an error, that it creates the directory,
// that it refuses rather than clobbers, and that the entry it writes is one the aify-wrapper package's
// parser accepts.
//
// It never touches ~/.aify — every run gets a temp registry path. A test that wrote the operator's
// real registry would be editing live configuration to check a code path, and this project has an
// incident from a suite that pointed at the live service and registered six agents into it.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { ENDPOINT_ENV_NAMES } from "../aify-service-endpoint.mjs";

const CLI = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "register-service-cli.mjs");

/** Set, and reachable by nothing. Never the live service. */
const NOWHERE = "http://127.0.0.2:1";

function run(registryPath, { endpoint = NOWHERE, bridgeDir = "/b/mcp/stdio" } = {}) {
  return spawnSync(process.execPath, [CLI, registryPath, endpoint, bridgeDir], {
    encoding: "utf8",
    timeout: 60_000,
  });
}

function withTempDir(body) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reg-"));
  try {
    return body(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("a host with no registry gets one, directory and all", () => {
  withTempDir((dir) => {
    // A nested path the installer has never created: the first service on a fresh host.
    const registry = path.join(dir, ".aify", "services.json");
    const res = run(registry);
    assert.equal(res.status, 0, `${res.stdout}\n${res.stderr}`);
    const parsed = JSON.parse(fs.readFileSync(registry, "utf8"));
    assert.deepEqual(Object.keys(parsed.services), ["aify-comms"]);
  });
});

test("the entry declares exactly the env names the bridge reads", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    assert.equal(run(registry).status, 0);
    const entry = JSON.parse(fs.readFileSync(registry, "utf8")).services["aify-comms"];
    assert.deepEqual(entry.endpointEnv, ENDPOINT_ENV_NAMES);
    assert.equal(entry.endpoint, NOWHERE);
  });
});

test("both MCP servers are registered, pointing into the installed bridge directory", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    assert.equal(run(registry, { bridgeDir: "/opt/aify/mcp/stdio" }).status, 0);
    const entry = JSON.parse(fs.readFileSync(registry, "utf8")).services["aify-comms"];
    assert.deepEqual(entry.mcp.map((s) => s.name).sort(), ["aify-comms", "aify-comms-channel"]);
    assert.equal(entry.mcp.find((s) => s.name === "aify-comms").args[0], "/opt/aify/mcp/stdio/server.js");
  });
});

test("another service's entry is still there afterwards", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    fs.writeFileSync(registry, JSON.stringify({
      version: 1,
      services: { "aify-graph": { endpoint: "http://g", endpointEnv: ["G_URL"], mcp: [] } },
    }));
    assert.equal(run(registry).status, 0);
    const parsed = JSON.parse(fs.readFileSync(registry, "utf8"));
    assert.deepEqual(Object.keys(parsed.services).sort(), ["aify-comms", "aify-graph"]);
  });
});

test("an unreadable registry is REFUSED and left byte-identical", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    const before = "{not json";
    fs.writeFileSync(registry, before);
    const res = run(registry);
    assert.notEqual(res.status, 0, "a registry we cannot read must not be rewritten");
    assert.equal(fs.readFileSync(registry, "utf8"), before, "the file was modified anyway");
  });
});

test("running it twice leaves the file byte-identical", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    assert.equal(run(registry).status, 0);
    const first = fs.readFileSync(registry, "utf8");
    assert.equal(run(registry).status, 0);
    assert.equal(fs.readFileSync(registry, "utf8"), first, "a reinstall changed the registry");
  });
});

test("missing arguments are refused rather than defaulted", () => {
  // Defaulting the endpoint would register this service as reachable somewhere nobody named.
  const res = spawnSync(process.execPath, [CLI], { encoding: "utf8", timeout: 60_000 });
  assert.equal(res.status, 78);
});
