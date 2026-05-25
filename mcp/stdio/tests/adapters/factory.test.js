import assert from "assert";
import test from "node:test";
import { adapterFor, supportedRuntimes } from "../../adapters/index.js";
import { ClaudeAdapter } from "../../adapters/claude.js";
import { CodexAdapter } from "../../adapters/codex.js";
import { HermesAdapter } from "../../adapters/hermes.js";
import { PiAdapter } from "../../adapters/pi.js";
import { OpencodeAdapter } from "../../adapters/opencode.js";

test("adapterFor returns ClaudeAdapter for claude-code", () => {
  assert.ok(adapterFor("claude-code") instanceof ClaudeAdapter);
});

test("adapterFor returns ClaudeAdapter for claude alias", () => {
  assert.ok(adapterFor("claude") instanceof ClaudeAdapter);
});

test("adapterFor returns CodexAdapter for codex", () => {
  assert.ok(adapterFor("codex") instanceof CodexAdapter);
});

test("adapterFor returns HermesAdapter for hermes", () => {
  assert.ok(adapterFor("hermes") instanceof HermesAdapter);
});

test("adapterFor returns PiAdapter for pi", () => {
  assert.ok(adapterFor("pi") instanceof PiAdapter);
});

test("adapterFor returns PiAdapter for omp alias", () => {
  assert.ok(adapterFor("omp") instanceof PiAdapter);
});

test("adapterFor returns PiAdapter for oh-my-pi alias", () => {
  assert.ok(adapterFor("oh-my-pi") instanceof PiAdapter);
});

test("adapterFor returns OpencodeAdapter for opencode", () => {
  assert.ok(adapterFor("opencode") instanceof OpencodeAdapter);
});

test("adapterFor is case-insensitive and trims whitespace", () => {
  assert.ok(adapterFor("  CLAUDE-CODE  ") instanceof ClaudeAdapter);
  assert.ok(adapterFor("Codex") instanceof CodexAdapter);
});

test("adapterFor throws on unknown runtime", () => {
  assert.throws(() => adapterFor("not-a-real-runtime"), /Unknown runtime/);
});

test("adapterFor throws on empty input", () => {
  assert.throws(() => adapterFor(""), /Unknown runtime/);
  assert.throws(() => adapterFor(null), /Unknown runtime/);
});

test("supportedRuntimes lists the five canonical names", () => {
  const names = supportedRuntimes();
  assert.deepStrictEqual([...names].sort(), [
    "claude-code", "codex", "hermes", "opencode", "pi",
  ]);
});
