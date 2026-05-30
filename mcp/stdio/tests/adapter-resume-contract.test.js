#!/usr/bin/env node
// Task 2.1 (2026-05-30 runtime-symmetry plan): the symmetric adapter contract.
// Every adapter MUST advertise `sessionIdSource ∈ {pinned,captured,resume}` and
// a `resumeCommand(sessionId)` that returns the operator takeover command for
// that runtime. hermes already had these (Task 1.3); this file pins the rest so
// a new harness follows one pattern. (The exhaustive symmetry-guard that
// iterates the registry is Task 2.2.)

import assert from "node:assert/strict";
import { test } from "node:test";

import { RuntimeAdapter } from "../adapters/base.js";
import { ClaudeAdapter } from "../adapters/claude.js";
import { CodexAdapter } from "../adapters/codex.js";
import { PiAdapter } from "../adapters/pi.js";
import { OpencodeAdapter } from "../adapters/opencode.js";
import { HermesAdapter } from "../adapters/hermes.js";

const VALID_SOURCES = new Set(["pinned", "captured", "resume"]);

// ─────────────────── base defaults are loud ───────────────────

test("base sessionIdSource is unset/loud so omissions are detectable", () => {
  const base = new RuntimeAdapter();
  // The default must be LOUD (throw) rather than silently return a valid value,
  // so an adapter that forgets to override it is detectable.
  assert.throws(() => base.sessionIdSource, /sessionIdSource|abstract/i);
});

test("base resumeCommand throws clearly when not overridden", () => {
  const base = new RuntimeAdapter();
  assert.throws(() => base.resumeCommand("anything"), /resumeCommand|abstract|not.*implemented/i);
});

// ─────────────────── claude ───────────────────

test("claude sessionIdSource is 'captured'", () => {
  assert.equal(new ClaudeAdapter().sessionIdSource, "captured");
});

test("claude resumeCommand returns the claude-aify takeover command", () => {
  assert.equal(
    new ClaudeAdapter().resumeCommand("abc-123"),
    "claude-aify --resume abc-123",
  );
});

// ─────────────────── codex ───────────────────

test("codex sessionIdSource is 'resume'", () => {
  assert.equal(new CodexAdapter().sessionIdSource, "resume");
});

test("codex resumeCommand returns the codex-aify takeover command", () => {
  assert.equal(
    new CodexAdapter().resumeCommand("thread-xyz"),
    "codex-aify --resume thread-xyz",
  );
});

// ─────────────────── pi ───────────────────

test("pi sessionIdSource is 'resume'", () => {
  assert.equal(new PiAdapter().sessionIdSource, "resume");
});

test("pi resumeCommand returns the pi-aify takeover command", () => {
  assert.equal(
    new PiAdapter().resumeCommand("omp-sess-1"),
    "pi-aify --resume omp-sess-1",
  );
});

// ─────────────────── opencode ───────────────────

test("opencode sessionIdSource is a valid enum value", () => {
  assert.ok(VALID_SOURCES.has(new OpencodeAdapter().sessionIdSource));
});

test("opencode resumeCommand returns the opencode-aify takeover command", () => {
  assert.equal(
    new OpencodeAdapter().resumeCommand("oc-1"),
    "opencode-aify --resume oc-1",
  );
});

// ─────────────────── hermes (already implemented; regression) ───────────────────

test("hermes sessionIdSource is 'pinned'", () => {
  assert.equal(new HermesAdapter().sessionIdSource, "pinned");
});

test("hermes resumeCommand returns the TUI takeover command", () => {
  assert.equal(
    new HermesAdapter().resumeCommand("aify-x"),
    "hermes --tui --resume aify-x",
  );
});

// ─────────────────── all concrete adapters satisfy the enum ───────────────────

test("every concrete adapter advertises a valid sessionIdSource + string resumeCommand", () => {
  for (const Adapter of [ClaudeAdapter, CodexAdapter, PiAdapter, OpencodeAdapter, HermesAdapter]) {
    const a = new Adapter();
    assert.ok(VALID_SOURCES.has(a.sessionIdSource), `${a.name} sessionIdSource invalid: ${a.sessionIdSource}`);
    const cmd = a.resumeCommand("sample-id");
    assert.equal(typeof cmd, "string", `${a.name} resumeCommand must return a string`);
    assert.ok(cmd.includes("sample-id"), `${a.name} resumeCommand must embed the session id`);
  }
});
