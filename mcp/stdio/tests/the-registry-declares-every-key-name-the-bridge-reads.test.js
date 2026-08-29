#!/usr/bin/env node
// Which env names carry the key that opens a service, and whether the registry declares them.
//
// THE SAME JOIN as `service-carriers-the-registry-does-not-declare.test.js`, on the other field, and
// it sat undone. A runtime's per-server MCP env block is KEY-SCOPED: the wrapper builds each block
// from what the registry declares, so a name the bridge READS but the registry does not DECLARE is
// inherited from whatever launched the runtime. For an endpoint that means talking to the wrong
// service; for a credential it means one service's key reaching another service's bridge, accepted
// or refused for reasons visible from neither side.
//
// FIVE MODULES TYPED THE PRECEDENCE OUT BY HAND before this — aify-http.mjs,
// aify-service-endpoint.mjs, claude-channel.js, hermes-channel.js and notify-check.js each carried
// `process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || ""` — and the registry declared
// neither name. Five copies of a rule is five chances for one of them to get a fix the others do not,
// which is what the comment above this file's sibling import in claude-channel.js already says about
// the URL helpers it forked the same way.
//
// A SIXTH COPY IS DELIBERATE AND MUST STAY: `fixtures/hermes-managed-host.before-gateway.js` is a
// frozen "before" snapshot. A test that swept it up would be rewriting history to satisfy itself.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

import { API_KEY_ENV_NAMES, ENDPOINT_ENV_NAMES, apiKeyFrom } from "../aify-service-endpoint.mjs";
import { upsertService } from "../service-registry.mjs";

const STDIO = fileURLToPath(new URL("..", import.meta.url));

/** Every bridge source, DERIVED by walking rather than listed: a new module joins the scan for free. */
function bridgeSources(dir = STDIO, found = []) {
  for (const name of readdirSync(dir)) {
    if (["node_modules", "tests", "fixtures", ".git"].includes(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) bridgeSources(full, found);
    else if (/\.(js|mjs)$/.test(name)) found.push(full);
  }
  return found;
}

test("only ONE module names the key carriers", () => {
  // The declaration itself is the one legitimate mention. Anything else is a fork.
  const offenders = bridgeSources()
    .filter((file) => !file.endsWith("aify-service-endpoint.mjs"))
    .filter((file) => readFileSync(file, "utf8").includes("CLAUDE_MCP_API_KEY"));
  assert.deepEqual(offenders.map((f) => f.slice(STDIO.length)), [],
    "a module re-typed the key precedence instead of calling apiKeyFrom()");
});

test("the scan can find something, so its empty answer means something", () => {
  // A walk that silently matched nothing would pass the test above forever. This proves the
  // instrument reaches the same files, using a name that IS still typed out in several of them.
  const withEndpointName = bridgeSources()
    .filter((file) => readFileSync(file, "utf8").includes("CLAUDE_MCP_SERVER_URL"));
  assert.ok(withEndpointName.length > 0, "the source walk found no files at all");
});

test("apiKeyFrom applies the precedence, and an absent key is an ANSWER", () => {
  assert.equal(apiKeyFrom({ CLAUDE_MCP_API_KEY: "first", AIFY_API_KEY: "second" }), "first");
  assert.equal(apiKeyFrom({ AIFY_API_KEY: "second" }), "second");
  // "" is not a failure: a service running without API_KEY set accepts unauthenticated calls, and a
  // caller must be able to say "I have none" rather than invent one.
  assert.equal(apiKeyFrom({}), "");
  assert.equal(apiKeyFrom({ CLAUDE_MCP_API_KEY: "" , AIFY_API_KEY: "second" }), "second",
    "an empty value is not a key, and must fall through to the next name");
});

test("every name the bridge reads is declared in the registry entry it writes", () => {
  const result = upsertService("", "aify-comms", {
    endpoint: "http://127.0.0.1:8800",
    endpointEnv: ENDPOINT_ENV_NAMES,
    keyEnv: API_KEY_ENV_NAMES,
    mcp: [{ name: "aify-comms", command: "node", args: ["/x/server.js"] }],
  });
  assert.equal(result.ok, true, result.errors.join("; "));
  const entry = JSON.parse(result.text).services["aify-comms"];
  assert.deepEqual(entry.keyEnv, API_KEY_ENV_NAMES,
    "the registry entry does not carry the names the bridge reads");
  // Not the same list as the endpoint's: binding a key name to an endpoint value, or the reverse,
  // would be silently wrong in a way no request could report.
  assert.notDeepEqual(entry.keyEnv, entry.endpointEnv);
});

test("an entry written without a key list is still valid, and says nothing", () => {
  // Absent is a legitimate state: a service with no API_KEY set has no key to declare, and writing an
  // empty list must not read as "this service refuses keys".
  const result = upsertService("", "aify-comms", {
    endpoint: "http://127.0.0.1:8800",
    endpointEnv: ENDPOINT_ENV_NAMES,
    mcp: [{ name: "aify-comms", command: "node", args: ["/x/server.js"] }],
  });
  assert.equal(result.ok, true, result.errors.join("; "));
  assert.deepEqual(JSON.parse(result.text).services["aify-comms"].keyEnv, []);
});
