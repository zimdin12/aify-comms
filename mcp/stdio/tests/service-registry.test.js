#!/usr/bin/env node
// Writing this service's entry into `~/.aify/services.json` without disturbing anyone else's.
//
// The registry is shared. aify-comms owns the `aify-comms` key and nothing else, and the failure this
// file exists to prevent is an aify-comms install quietly removing a service somebody else registered
// — which would not show up here at all. It would show up much later, as launchers installed after
// that point silently missing a service they used to carry.

import assert from "node:assert/strict";
import { test } from "node:test";

import { upsertService, REGISTRY_VERSION } from "../service-registry.mjs";
import { ENDPOINT_ENV_NAMES } from "../aify-service-endpoint.mjs";

const COMMS = {
  endpoint: "http://127.0.0.2:1",
  endpointEnv: ENDPOINT_ENV_NAMES,
  mcp: [
    { name: "aify-comms", command: "node", args: ["/b/server.js"] },
    { name: "aify-comms-channel", command: "node", args: ["/b/claude-channel.js"] },
  ],
};

test("an absent registry becomes a registry holding just this service", () => {
  const { ok, text } = upsertService("", "aify-comms", COMMS);
  assert.equal(ok, true);
  const parsed = JSON.parse(text);
  assert.equal(parsed.version, REGISTRY_VERSION);
  assert.deepEqual(Object.keys(parsed.services), ["aify-comms"]);
  assert.equal(parsed.services["aify-comms"].endpoint, "http://127.0.0.2:1");
});

test("ANOTHER service's entry survives an aify-comms install", () => {
  // The one that matters. Losing it here means launchers installed later silently drop that service,
  // and nothing about the aify-comms install would look wrong.
  const existing = JSON.stringify({
    version: 1,
    services: {
      "aify-graph": { endpoint: "http://g", endpointEnv: ["G_URL"], mcp: [{ name: "g", command: "node", args: [] }] },
    },
  });
  const { ok, text } = upsertService(existing, "aify-comms", COMMS);
  assert.equal(ok, true);
  const parsed = JSON.parse(text);
  assert.deepEqual(Object.keys(parsed.services).sort(), ["aify-comms", "aify-graph"]);
  assert.equal(parsed.services["aify-graph"].endpoint, "http://g");
  assert.deepEqual(parsed.services["aify-graph"].mcp[0], { name: "g", command: "node", args: [] });
});

test("reinstalling REPLACES this service's entry rather than accumulating", () => {
  const first = upsertService("", "aify-comms", COMMS).text;
  const second = upsertService(first, "aify-comms", { ...COMMS, endpoint: "http://127.0.0.2:2" }).text;
  const parsed = JSON.parse(second);
  assert.deepEqual(Object.keys(parsed.services), ["aify-comms"]);
  assert.equal(parsed.services["aify-comms"].endpoint, "http://127.0.0.2:2");
});

test("the same inputs produce a BYTE-IDENTICAL file", () => {
  // Otherwise every reinstall changes the registry, which changes the fingerprint baked into every
  // launcher, which would report every wrapper stale after an unrelated reinstall.
  assert.equal(upsertService("", "aify-comms", COMMS).text, upsertService("", "aify-comms", COMMS).text);
});

test("key order in the input does not change the output", () => {
  const a = upsertService(JSON.stringify({ version: 1, services: { z: { endpoint: "z" }, a: { endpoint: "a" } } }), "aify-comms", COMMS).text;
  const b = upsertService(JSON.stringify({ version: 1, services: { a: { endpoint: "a" }, z: { endpoint: "z" } } }), "aify-comms", COMMS).text;
  assert.equal(a, b);
});

test("an UNREADABLE registry is refused, never overwritten", () => {
  // Overwriting would uninstall whatever another service had registered, at the moment somebody
  // reinstalls something unrelated to it.
  const r = upsertService("{not json", "aify-comms", COMMS);
  assert.equal(r.ok, false);
  assert.equal(r.text, undefined);
  assert.match(r.errors.join(" "), /unreadable/);
});

test("a registry at another VERSION is refused rather than migrated", () => {
  const r = upsertService(JSON.stringify({ version: 99, services: {} }), "aify-comms", COMMS);
  assert.equal(r.ok, false);
  assert.equal(r.text, undefined);
});

test("strictMcp is written only when it is true", () => {
  // An opt-in nobody chose must not appear in the file as though somebody decided it.
  assert.equal(JSON.parse(upsertService("", "aify-comms", COMMS).text).services["aify-comms"].strictMcp, undefined);
  const opted = upsertService("", "aify-comms", { ...COMMS, strictMcp: true }).text;
  assert.equal(JSON.parse(opted).services["aify-comms"].strictMcp, true);
});

test("the endpoint env names come from the bridge, not from this test", () => {
  // Typed by hand in either place, they drift; and a name the bridge reads but the registry does not
  // declare is INHERITED from whatever launched the runtime, because a per-server MCP env block is
  // key-scoped. That failure looks like everything working until two services disagree.
  const written = JSON.parse(upsertService("", "aify-comms", COMMS).text).services["aify-comms"];
  assert.deepEqual(written.endpointEnv, ENDPOINT_ENV_NAMES);
  assert.ok(ENDPOINT_ENV_NAMES.length > 0, "the bridge must declare at least one endpoint name");
});
