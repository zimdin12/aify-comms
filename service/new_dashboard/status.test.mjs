#!/usr/bin/env node
// Tests for the canonical status resolver (status.js / F2). Imported DIRECTLY — these are
// real behavior assertions, replacing the old source-grep snapshots in app.test.mjs.
//
// Run: node --test service/new_dashboard/status.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { STATUS_KINDS, resolveStatus, renderStatusChip, renderStatusDot } from "./status.js";

test("the 8-label agent contract is all mapped (never renders unknown)", () => {
  for (const s of ["working", "online", "idle", "available", "blocked", "stale", "offline", "stopped"]) {
    assert.ok(STATUS_KINDS[s], `STATUS_KINDS must map the contract status '${s}'`);
    assert.equal(resolveStatus(s).kind, s, `'${s}' must resolve to its own kind, not unknown`);
  }
});

test("ready and active are internal aliases that render as online", () => {
  assert.equal(resolveStatus("ready").label, "online", "ready must display as online");
  assert.equal(resolveStatus("ready").dotKind, "online");
  // `active` is a legacy alias the server may still emit; it must render positively, not unknown.
  assert.equal(resolveStatus("active").kind, "active");
  assert.notEqual(resolveStatus("active").dotKind, "unknown");
});

test("run/contract lifecycle statuses are mapped, not unknown", () => {
  for (const s of ["queued", "claimed", "running", "completed", "failed", "cancelled", "lost", "recovering", "unreachable"]) {
    assert.equal(resolveStatus(s).kind, s, `lifecycle status '${s}' must map to itself`);
  }
});

test("an unrecognized raw status falls back to unknown without throwing", () => {
  assert.equal(resolveStatus("frobnicate").kind, "unknown");
  assert.equal(resolveStatus("").kind, "unknown");
  assert.equal(resolveStatus(null).kind, "unknown");
  assert.equal(resolveStatus(undefined).kind, "unknown");
});

test("context may override the label and attach badges", () => {
  const r = resolveStatus("online", { label: "online · awaiting reply", badges: ["⚒ subagents", ""] });
  assert.equal(r.label, "online · awaiting reply");
  assert.deepEqual(r.badges, ["⚒ subagents"], "falsy badges are filtered");
});

test("renderStatusChip escapes interpolated values (XSS guard)", () => {
  const html = renderStatusChip("online", { why: '"><img src=x onerror=alert(1)>' });
  assert.ok(!html.includes("<img"), "chip must escape attribute-context HTML");
  assert.ok(html.includes("&quot;") || html.includes("&gt;"), "escaped entities present");
  assert.ok(html.includes('data-status-kind="online"'));
});

test("renderStatusDot reflects the resolved dotKind", () => {
  assert.ok(renderStatusDot("blocked").includes("status-dot dot blocked"));
  assert.ok(renderStatusDot("offline").includes("offline"));
  assert.ok(renderStatusDot("frobnicate").includes("unknown"));
});
