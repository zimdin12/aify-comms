#!/usr/bin/env node
// A service that demands a key, and a client that holds none.
//
// BUILT FROM A WRONG DIAGNOSIS, and that is worth stating where the next reader will meet it. On
// 2026-09-07 a hermes agent was 401ing on every outbound call and a `grep -A 20` of the aify-comms
// block in `~/.hermes/config.yaml` found no API key. The key was on line 22 -- the window ended at
// 20 -- and the config's key authenticates against the live service. That agent's 401 is still
// UNEXPLAINED; it was not this.
//
// WHAT IS STILL TRUE, and why the check stayed: the service does refuse unauthenticated calls, a
// keyless client would 401 on every call, and no existing row would report it. Claude and hermes keep
// their keys in different files, so half a fleet can go mute while every badge stays green -- a quiet
// agent looks exactly like an idle one, since `lastSeen` refreshes on registration and `Last
// produced` only records when an agent last SENT.
//
// SO THESE TESTS RULE OUT THE SHAPE THAT PRODUCED THE MISTAKE: a check that reports because it did
// not look properly. The parse is controlled BOTH ways -- it must find a key that is there, and still
// say no when the key is absent, empty, or belongs to a different MCP server in the same file -- and
// it walks the entry to its end by indentation rather than sampling a fixed window after it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { clientApiKeyVerdict, entryCarriesKey } from "../client-api-key-check.mjs";

const HERMES_WITH_KEY = [
  "mcp_servers:",
  "  aify-comms:",
  "    command: \"node\"",
  "    env:",
  "      AIFY_AGENT_ID: \"${AIFY_AGENT_ID}\"",
  "      AIFY_API_KEY: \"abc123\"",
  "      AIFY_SERVER_URL: \"http://127.0.0.1:8800\"",
].join("\n");

// The shape a keyless entry has: the env vars an install writes, with no key among them. NOT a
// copy of the operator's file -- theirs carries a key, which is the correction above.
const HERMES_WITHOUT_KEY = [
  "mcp_servers:",
  "  aify-comms:",
  "    command: \"node\"",
  "    env:",
  "      AIFY_AGENT_ID: \"${AIFY_AGENT_ID}\"",
  "      AIFY_SESSION_MODE: \"${AIFY_SESSION_MODE}\"",
  "      AIFY_SERVER_URL: \"http://127.0.0.1:8800\"",
].join("\n");

// ── the parse ───────────────────────────────────────────────────────────────────────────────────

test("POSITIVE CONTROL: it finds a key that is there", () => {
  // Every assertion below is about saying NO. A parse that always returned false would satisfy them
  // all and report the whole fleet broken, which is the opposite failure and just as useless.
  assert.equal(entryCarriesKey(HERMES_WITH_KEY, "yaml"), true);
  assert.equal(entryCarriesKey(JSON.stringify({
    mcpServers: { "aify-comms": { env: { AIFY_API_KEY: "abc123" } } },
  }), "json"), true);
});

test("a keyless entry reads as keyless", () => {
  assert.equal(entryCarriesKey(HERMES_WITHOUT_KEY, "yaml"), false);
});

test("either key name counts, because the bridge reads both", () => {
  // Derived from API_KEY_ENV_NAMES rather than typed here — a check that knew a different set of
  // names than the bridge would disagree with it the day one is added.
  const withClaudeName = HERMES_WITH_KEY.replace("AIFY_API_KEY", "CLAUDE_MCP_API_KEY");
  assert.equal(entryCarriesKey(withClaudeName, "yaml"), true);
});

test("AN EMPTY VALUE IS NOT A KEY", () => {
  // `AIFY_API_KEY: ""` satisfies a name search and authenticates with nothing — exactly the state
  // this check exists to find, wearing the costume of a fix.
  for (const empty of ['AIFY_API_KEY: ""', "AIFY_API_KEY: ''", "AIFY_API_KEY:"]) {
    const text = HERMES_WITH_KEY.replace('AIFY_API_KEY: "abc123"', empty);
    assert.equal(entryCarriesKey(text, "yaml"), false, `${empty} was accepted as a key`);
  }
});

test("A KEY ON SOMEBODY ELSE'S SERVER DOES NOT COUNT", () => {
  // ~/.claude.json holds every MCP server the operator ever installed. A file-wide search passes on
  // a key belonging to another server and calls our entry healthy.
  const yaml = [
    "mcp_servers:",
    "  some-other-tool:",
    "    env:",
    "      AIFY_API_KEY: \"belongs-to-someone-else\"",
    "  aify-comms:",
    "    env:",
    "      AIFY_SERVER_URL: \"http://127.0.0.1:8800\"",
  ].join("\n");
  assert.equal(entryCarriesKey(yaml, "yaml"), false, "a neighbouring server's key was counted as ours");

  const json = JSON.stringify({
    mcpServers: {
      "other": { env: { AIFY_API_KEY: "belongs-to-someone-else" } },
      "aify-comms": { env: { AIFY_SERVER_URL: "http://127.0.0.1:8800" } },
    },
  });
  assert.equal(entryCarriesKey(json, "json"), false);
});

test("no entry, or an unparseable file, is NULL — not false", () => {
  // "I could not tell" and "there is no key" lead to different verdicts, and collapsing them would
  // report a missing config as a broken one.
  assert.equal(entryCarriesKey("mcp_servers:\n  other:\n    env: {}", "yaml"), null);
  assert.equal(entryCarriesKey("{not json", "json"), null);
  assert.equal(entryCarriesKey("", "yaml"), null);
});

// ── the verdict ─────────────────────────────────────────────────────────────────────────────────

const KEYED = { name: "claude", path: "~/.claude.json", carriesKey: true };
const KEYLESS = { name: "hermes", path: "~/.hermes/config.yaml", carriesKey: false };

test("THE COMBINATION: a keyed service plus a keyless client FAILS and names it", () => {
  const v = clientApiKeyVerdict({ serviceRequiresKey: true, clients: [KEYED, KEYLESS] });
  assert.equal(v.ok, false);
  assert.equal(v.code, "client-has-no-key");
  assert.match(v.detail, /hermes/);
  assert.match(v.detail, /401/, "the row does not say what the operator will actually see");
  assert.match(v.fix, /install\.sh --client hermes/, "the fix does not name the one command that repairs it");
});

test("A SERVICE WITH NO KEY IS NOT A DEFECT", () => {
  // Running open is a configuration. Firing here would make this row red on every developer machine
  // that never set API_KEY, which is how a check gets switched off before the day it matters.
  const v = clientApiKeyVerdict({ serviceRequiresKey: false, clients: [KEYLESS] });
  assert.equal(v.ok, true);
  assert.equal(v.code, "no-key-required");
});

test("both halves present is a pass", () => {
  const v = clientApiKeyVerdict({ serviceRequiresKey: true, clients: [KEYED] });
  assert.equal(v.ok, true);
  assert.equal(v.code, "keys-present");
});

test("NO EVIDENCE IS NOT A PASS — either half missing reads unknown-all", () => {
  // The rule this repo already paid for twice (`env-bridge`, `bridge-current`): a check that
  // gathered nothing must not look like one that verified something.
  for (const deps of [
    { serviceRequiresKey: null, clients: [KEYLESS] },
    { serviceRequiresKey: true, clients: null },
  ]) {
    const v = clientApiKeyVerdict(deps);
    assert.equal(v.ok, false, `${JSON.stringify(deps)} reported ok`);
    assert.equal(v.code, "unknown-all");
  }
  assert.equal(clientApiKeyVerdict().code, "unknown-all", "called with nothing, it claimed something");
});

test("an unreadable client is PARTIAL, not a pass and not a failure", () => {
  // It has some evidence (one client verified) and a gap, which is a third state — collapsing it
  // into either neighbour loses the distinction `no-evidence-is-not-a-pass` exists to keep.
  const v = clientApiKeyVerdict({
    serviceRequiresKey: true,
    clients: [KEYED, { name: "hermes", path: "~/.hermes/config.yaml", carriesKey: null }],
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "partial");
  assert.match(v.detail, /hermes/);
});

test("a host with no client installed is not broken", () => {
  const v = clientApiKeyVerdict({ serviceRequiresKey: true, clients: [] });
  assert.equal(v.ok, true);
  assert.equal(v.code, "none-installed");
});
