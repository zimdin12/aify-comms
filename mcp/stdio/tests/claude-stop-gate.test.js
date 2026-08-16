#!/usr/bin/env node
// Tests for the claude Stop-gate decision (SECONDARY pure-event fix). The gate suppresses a
// Stop ONLY when classify(summarizeTranscriptTail(tail)) === "in-flight"; everything else
// (ended / unknown / any error) falls through to POST /turn-end. We test that decision via the
// two pure functions the gate composes — no network, no DOM.
//
// Run: node --test mcp/stdio/claude-stop-gate.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { classify } from "../turn-end-detector.js";
import { summarizeTranscriptTail } from "../adapters/claude.js";

const decision = (tail) => classify(summarizeTranscriptTail(tail)); // what the gate computes
const jl = (obj) => JSON.stringify(obj) + "\n";

test("gate POSTs (ended): assistant yielded with a terminal stop_reason, no pending tool", () => {
  const tail = jl({ type: "assistant", message: { role: "assistant", stop_reason: "end_turn", content: [{ type: "text", text: "done" }] } });
  assert.equal(decision(tail), "ended");
});

test("gate SUPPRESSES (in-flight): a premature Stop while a tool_use is pending", () => {
  const tail = jl({ type: "assistant", message: { role: "assistant", stop_reason: "tool_use", content: [{ type: "tool_use", id: "t1", name: "Bash" }] } });
  assert.equal(decision(tail), "in-flight");
});

test("gate SUPPRESSES (in-flight): trailing tool_result/user feeding the next step", () => {
  const tail = jl({ type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "ok" }] } });
  assert.equal(decision(tail), "in-flight");
});

test("gate POSTs (unknown): empty / unreadable tail never suppresses (fail-safe)", () => {
  assert.equal(decision(""), "unknown");
  assert.equal(decision("not json\n{also not\n"), "unknown");
});

test("gate POSTs (ended): the LAST message decides — a completed turn after earlier tool calls", () => {
  const tail =
    jl({ type: "assistant", message: { role: "assistant", stop_reason: "tool_use", content: [{ type: "tool_use", id: "t1", name: "Read" }] } }) +
    jl({ type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "x" }] } }) +
    jl({ type: "assistant", message: { role: "assistant", stop_reason: "end_turn", content: [{ type: "text", text: "all done" }] } });
  assert.equal(decision(tail), "ended");
});
