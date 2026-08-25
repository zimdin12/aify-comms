#!/usr/bin/env node
// Which env names decide "which aify service answers me", and which of them the registry declares.
//
// THE JOIN. `ENDPOINT_ENV_NAMES` is exported from `aify-service-endpoint.mjs` and consumed by
// `register-service-cli.mjs`, which writes it into the shared registry at `~/.aify/services.json` as
// `endpointEnv`. The reader lives in the aify-wrapper package: `mcpEntriesFor()` builds each MCP
// server's env block as `Object.fromEntries(service.endpointEnv.map(k => [k, service.endpoint]))`.
// A runtime's per-server MCP env block is KEY-SCOPED, so a name the registry does not declare is
// INHERITED from whatever launched the runtime rather than set per service.
//
// MEASURED 2026-08-26, by running this repo's declaration through the wrapper's own reader: the
// per-server env block for aify-comms comes back as exactly ["CLAUDE_MCP_SERVER_URL",
// "AIFY_SERVER_URL"]. Neither fallback name appears in any block.
//
// So the two resolver modules read FOUR names that select a service and the registry declares TWO.
// The undeclared pair is `CLAUDE_MCP_FALLBACK_URLS` / `AIFY_SERVER_FALLBACK_URLS`, and this test
// pins that difference rather than hiding it.
//
// WHY THE GAP IS NOT CLOSED, which is the part worth reading. `endpointEnv` binds every declared name
// to the service's ENDPOINT value, so declaring the fallback pair would set the fallback list to the
// primary URL -- inert after `uniqueServerUrls` dedupes it, and it would also silently OVERRIDE the
// operator's documented escape hatch ("Set AIFY_SERVER_FALLBACK_URLS / CLAUDE_MCP_FALLBACK_URLS to
// opt into any non-loopback fallback explicitly", aify-service-endpoint.mjs). Nothing in this repo
// produces those vars -- not install.sh, not a wrapper template -- so their only use today is an
// operator setting them by hand, which is exactly what declaring them would break. Trading a live
// documented feature for a hypothetical is the wrong side of that deal while ONE service exists.
//
// WHAT WOULD CHANGE THE ANSWER: a second registered service. `httpCall` iterates
// [ACTIVE_SERVER_URL, ...SERVER_URLS] and LATCHES `ACTIVE_SERVER_URL` to the first URL that answers,
// so an inherited fallback pointing at another service becomes this process's endpoint for the rest
// of its life. The comment above `defaultFallbackServerUrls` records that exact class already
// happening once: fallbacks "silently failed a local bridge over to a developer's shared server".
//
// This test is therefore a DECISION GATE, not a bug report. It fails when a new service-selecting
// carrier appears in either resolver, and hands whoever added it the trade-off above.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

import { ENDPOINT_ENV_NAMES } from "../aify-service-endpoint.mjs";

const STDIO = fileURLToPath(new URL("..", import.meta.url));

// The two modules that resolve which aify service this process talks to. Scoped deliberately: the
// runtime adapters also read names ending in SERVER_URL (AIFY_CODEX_APP_SERVER_URL,
// AIFY_HERMES_APISERVER_URL), and those select a RUNTIME's own server, not this service. A pattern
// wide enough to catch them would need an exclusion list, which is a list someone must remember to
// update -- so the scope is the two files that build the URL set instead.
const RESOLVERS = ["aify-service-endpoint.mjs", "claude-channel.js"];

// Names that answer "which service answers me". Written as two alternatives rather than one loose
// pattern so a name like AIFY_HTTP_TIMEOUT_MS cannot drift in.
const SELECTS_A_SERVICE = /^[A-Z][A-Z0-9_]*(?:SERVER_URL|FALLBACK_URLS)$/;

const READ_LITERAL = /process\.env\.([A-Z][A-Z0-9_]*)/g;

const UNDECLARED_ON_PURPOSE = ["AIFY_SERVER_FALLBACK_URLS", "CLAUDE_MCP_FALLBACK_URLS"];

function carriersReadBy(fileName) {
  const source = readFileSync(join(STDIO, fileName), "utf8");
  const names = new Set();
  for (const match of source.matchAll(READ_LITERAL)) {
    if (SELECTS_A_SERVICE.test(match[1])) names.add(match[1]);
  }
  return names;
}

function allCarriers() {
  const names = new Set(ENDPOINT_ENV_NAMES);
  for (const file of RESOLVERS) for (const name of carriersReadBy(file)) names.add(name);
  return [...names].sort();
}

test("the scanner finds carriers that are really there", () => {
  // Positive control. Every assertion below is about a DIFFERENCE between two sets, and an empty
  // scan would make that difference look decided when nothing was measured.
  const found = carriersReadBy("claude-channel.js");
  for (const name of ["CLAUDE_MCP_SERVER_URL", "AIFY_SERVER_URL", ...UNDECLARED_ON_PURPOSE]) {
    assert.ok(found.has(name), `the scanner missed ${name}, which claude-channel.js reads literally`);
  }
});

test("the scanner can say absent", () => {
  // Negative control. A pattern that matched everything would satisfy the positive control too.
  const found = carriersReadBy("claude-channel.js");
  for (const name of ["AIFY_HTTP_TIMEOUT_MS", "AIFY_AGENT_ID", "CLAUDE_MCP_API_KEY"]) {
    assert.ok(!found.has(name), `${name} does not select a service, but the scanner claimed it does`);
  }
});

test("aify-service-endpoint reads its primary pair through the exported constant, not by name", () => {
  // The reason the endpoint module shows only the fallbacks as literals: the primary pair is read as
  // ENDPOINT_ENV_NAMES.map(name => process.env[name]), which is what makes one list serve both the
  // bridge and the registry. If someone re-types either name here, this fails and says why.
  const literal = carriersReadBy("aify-service-endpoint.mjs");
  for (const name of ENDPOINT_ENV_NAMES) {
    assert.ok(
      !literal.has(name),
      `${name} is now read by name in aify-service-endpoint.mjs as well as through ` +
        "ENDPOINT_ENV_NAMES; that is a second copy of the list the registry declares from",
    );
  }
});

test("every service-selecting carrier is either declared to the registry or listed as a known gap", () => {
  const carriers = allCarriers();
  const undeclared = carriers.filter((name) => !ENDPOINT_ENV_NAMES.includes(name));
  assert.deepEqual(
    undeclared.sort(),
    [...UNDECLARED_ON_PURPOSE].sort(),
    "a service-selecting env name is read by a resolver and not declared in ENDPOINT_ENV_NAMES, so " +
      "the wrapper cannot key-scope it and it will be INHERITED from whatever launched the runtime. " +
      "Declaring it binds the name to the service endpoint, which also overrides the operator's " +
      "documented fallback opt-in -- so this is a decision, not a repair. Read the header.",
  );
});

test("the declared pair is what the registry actually receives", () => {
  // The other half of the join, held against the CLI that writes the entry rather than against a
  // second copy of the list. A registry entry built from a different source would make every
  // assertion above true and still point the fleet somewhere else.
  const cli = readFileSync(join(STDIO, "register-service-cli.mjs"), "utf8");
  assert.match(
    cli,
    /endpointEnv:\s*ENDPOINT_ENV_NAMES/,
    "register-service-cli.mjs no longer writes ENDPOINT_ENV_NAMES into the registry entry, so the " +
      "bridge and the registry can now disagree about which names carry the endpoint",
  );
});
