#!/usr/bin/env node
// The control-completion receipt must name the CHANNEL, not a "resident session" (v0.2.0).
//
// Why this test exists. `claude-channel.js` completed every control (interrupt/steer) with
// `response: "Delivered to Claude resident session"`. That loop does NOT branch on
// session_mode — it runs for resident AND managed claude agents — and the controls API
// surfaces `response_text` back to callers verbatim (api_v2.py ~18935/19037). So a MANAGED
// agent's control read back as a resident-session delivery.
//
// That mislabel cost a real diagnosis: it was read as evidence that managed WORK takes the
// resident delivery path, which became the leading hypothesis for the still-open restart bug
// and gated a v0.2 workstream on it. The hypothesis was withdrawn once the string was traced
// to this line. Actual brief delivery never says this — `markDispatchDelivered` writes
// "Delivered to Claude channel bridge" with a deliberately EMPTY summary (D2/#162).
//
// Source-level assertions on purpose: the control loop is inside `pollLoop`, which needs a
// live server and a channel transport to drive, so pinning the literal is what a test can
// honestly do here. The point is to make a drift back to "resident" fail the suite.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const SOURCE = readFileSync(new URL("../claude-channel.js", import.meta.url), "utf8");

test("the control receipt does not claim a resident session", () => {
  assert.ok(
    !/response:\s*"Delivered to Claude resident session"/.test(SOURCE),
    "the control receipt must not claim a resident session — it fires for managed agents too, " +
      "and this exact string sent a restart-bug diagnosis down a dead end",
  );
});

// The receipt sits after a long explanatory comment, so slice from the PATCH by index
// rather than matching across it with a bounded regex window.
function controlReceiptLiteral() {
  const start = SOURCE.indexOf('"PATCH", `/dispatch/controls/');
  assert.ok(start > 0, "the control-completion PATCH must still exist");
  const block = SOURCE.slice(start, start + 2000);
  const match = block.match(/response:\s*"([^"]+)"/);
  assert.ok(match, "the control-completion PATCH must still set a response");
  return match[1];
}

test("the control receipt names the channel and marks itself a control", () => {
  const receipt = controlReceiptLiteral();
  assert.match(receipt, /channel/i, `the receipt must name the channel — got ${JSON.stringify(receipt)}`);
  assert.match(receipt, /control/i, `the receipt must say it is a control, not a delivery — got ${JSON.stringify(receipt)}`);
});

test("run delivery and control completion do not share a receipt string", () => {
  // A control receipt that reads like a run delivery receipt is how the two got conflated.
  const controlReceipt = controlReceiptLiteral();

  const deliveryLiterals = [...SOURCE.matchAll(/appendEvent:[\s\S]{0,200}?"([^"]*Delivered[^"]*)"/g)].map((m) => m[1]);
  assert.ok(deliveryLiterals.length > 0, "run-delivery event literals must be findable");
  for (const literal of deliveryLiterals) {
    assert.notEqual(controlReceipt, literal, "control receipt must be distinct from run-delivery text");
  }
});

test("the two service-side receipt prefixes have no producer here", () => {
  // api_v2.py still carries CLAUDE_RESIDENT/CHANNEL_DELIVERY_SUMMARY_PREFIX and matches them
  // against run SUMMARIES. Measured 2026-08-07 on the live DB: zero rows carry either prefix,
  // because D2/#162 changed routine deliveries to an empty summary. Those branches are dead,
  // and carded as dead rather than fixed (KNOWN_ISSUES). This test documents the producer side
  // of that: if someone reintroduces a summary receipt here, the branches wake up and this
  // failure is the prompt to re-check them.
  assert.ok(
    !/summary:\s*"Delivered to Claude (resident|channel) session/.test(SOURCE),
    "reintroducing a delivery-receipt SUMMARY revives dead branches in api_v2.py — " +
      "re-check _is_delivery_only_claude_run and the /stats reply_pending exclusion first",
  );
});
