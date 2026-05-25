import assert from "assert";
import test from "node:test";

import { ClaudeAdapter } from "../../adapters/claude.js";
import { CodexAdapter } from "../../adapters/codex.js";
import { HermesAdapter } from "../../adapters/hermes.js";
import { PiAdapter } from "../../adapters/pi.js";
import { OpencodeAdapter } from "../../adapters/opencode.js";

test("ClaudeAdapter exposes controllerFor", () => {
  const a = new ClaudeAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("CodexAdapter exposes controllerFor", () => {
  const a = new CodexAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("HermesAdapter exposes controllerFor", () => {
  const a = new HermesAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("PiAdapter exposes controllerFor", () => {
  const a = new PiAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("OpencodeAdapter exposes controllerFor", () => {
  const a = new OpencodeAdapter();
  assert.strictEqual(typeof a.controllerFor, "function");
});

test("Pi resident mode returns null (Plan 2 flip)", () => {
  const a = new PiAdapter();
  const c = a.controllerFor({ runtime: "pi", executionMode: "resident", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  assert.ok(c === null || c === undefined, `pi resident must return null/undefined; got ${c}`);
});
