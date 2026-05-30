#!/usr/bin/env node
// Unit tests for the pinned-session-id derivation. Both the hermes sidecar
// and the hermes adapter import pinnedSessionId so the per-agent api_server
// session id is byte-identical everywhere.

import assert from "node:assert/strict";
import { test } from "node:test";
import { pinnedSessionId } from "../hermes-session-id.js";

test("pinnedSessionId is deterministic for the same agentId", () => {
  assert.equal(pinnedSessionId("sc-coder"), pinnedSessionId("sc-coder"));
});

test("pinnedSessionId prefixes aify- and preserves safe characters", () => {
  assert.equal(pinnedSessionId("sc-coder_1"), "aify-sc-coder_1");
});

test("pinnedSessionId sanitizes unsafe characters to the safe charset", () => {
  const id = pinnedSessionId("team/coder@host:1");
  assert.match(id, /^aify-[a-zA-Z0-9_-]+$/);
  // No slashes, @, or colons survive.
  assert.doesNotMatch(id, /[/@:]/);
});

test("pinnedSessionId is stable across runs (no randomness)", () => {
  const a = pinnedSessionId("alpha-bravo");
  const b = pinnedSessionId("alpha-bravo");
  assert.equal(a, b);
  assert.equal(a, "aify-alpha-bravo");
});
