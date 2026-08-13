#!/usr/bin/env node
// Tests for the canonical status resolver (status.js / F2). Imported DIRECTLY — these are
// real behavior assertions, replacing the old source-grep snapshots in app.test.mjs.
//
// Run: node --test service/new_dashboard/status.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  STATUS_KINDS,
  renderStatusChip,
  renderStatusDot,
  resolveStatus,
  runStatusContext,
  statusWhyContext,
} from './status.js';

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

// ── statusWhyContext / runStatusContext, moved here from app.js in v0.5.4 ────────────────────────
//
// `resolveStatus` decides WHAT a status is; these explain it. They live in the same module because a chip
// whose label and whose tooltip came from different files is exactly how the two end up disagreeing.

test("statusWhyContext names the thing, its state, and the details that identify it", () => {
  const why = statusWhyContext('session', {
    agentId: 'agent-a', environmentId: 'env-1', runtime: 'codex', workspace: '/w',
  }, 'online');
  assert.match(why.why, /agent-a/, "the tooltip must name which session it is about");
  assert.match(why.why, /env-1/, "…its environment");
  assert.match(why.why, /codex/, "…its runtime");
  assert.match(why.why, /\/w/, "…and its workspace");
});

test("it reads records through the shared field readers, so every API spelling works", () => {
  // The whole reason the readers are shared: a snake_case record must explain itself the same way a
  // camelCase one does, or the tooltip silently says "unknown" for half the rows.
  const camel = statusWhyContext('session', { agentId: 'a-1', environmentId: 'e', runtime: 'pi' }, 'online');
  const snake = statusWhyContext('session', { agent_id: 'a-1', environment_id: 'e', runtime: 'pi' }, 'online');
  assert.match(camel.why, /a-1/);
  assert.match(snake.why, /a-1/, "a snake_case record must identify itself too");
});

test("an unknown record still explains itself rather than rendering blanks", () => {
  // These strings go into a tooltip an operator reads while something is wrong. "undefined" or an empty
  // body is worse than "unknown" because it looks like the tooltip is broken rather than the agent.
  for (const kind of ['session', 'run', 'message', 'contract', 'environment', 'nonsense']) {
    const why = statusWhyContext(kind, {}, 'unknown');
    assert.equal(typeof why.why, 'string');
    assert.ok(why.why.trim().length > 0, `${kind} produced an empty explanation`);
    assert.doesNotMatch(why.why, /undefined|\[object Object\]|NaN/, `${kind} leaked a placeholder: ${why.why}`);
  }
});

test("runStatusContext surfaces a blocker ONLY when the status is actually blocked", () => {
  // The badge drives what the inspector offers. Showing "blocked" for a run that merely carries a stale
  // error string would offer an unblock action for a run that is running.
  const blocked = runStatusContext({ status: 'blocked', blockedByActiveRun: 'run-7' });
  assert.equal(blocked.blockerReason, 'run-7');
  assert.deepEqual(blocked.badges, ['blocked']);

  const running = runStatusContext({ status: 'running', error: 'an old error' });
  assert.equal(running.blockerReason, 'an old error', "the reason is still reported…");
  assert.deepEqual(running.badges, [], "…but a running run must NOT be badged blocked");
});

test("runStatusContext reads every spelling of a blocker and never returns undefined", () => {
  assert.equal(runStatusContext({ status: 'blocked', blockedBy: 'x' }).blockerReason, 'x');
  assert.equal(runStatusContext({ status: 'blocked', error: 'y' }).blockerReason, 'y');
  for (const run of [undefined, null, {}, { status: 'running' }]) {
    const out = runStatusContext(run);
    assert.equal(typeof out.blockerReason, 'string', `${JSON.stringify(run)} must give a string`);
    assert.equal(out.label, run?.status || 'unknown');
    assert.ok(Array.isArray(out.badges));
  }
});
