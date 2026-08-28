#!/usr/bin/env node
// Tests for the canonical status resolver (status.js / F2). Imported DIRECTLY — these are
// real behavior assertions, replacing the old source-grep snapshots in app.test.mjs.
//
// Run: node --test service/new_dashboard/status.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  AGENT_STATUSES,
  STATUS_KINDS,
  renderStatusChip,
  renderStatusDot,
  resolveStatus,
  runStatusContext,
  statusWhyContext,
} from './status.js';

test("every status in the vocabulary renders as itself, never as unknown", () => {
  // DERIVED from AGENT_STATUSES, which is bound to the Python owner and through it to
  // service/contracts/vocabulary.json. This test used to hand-type its own eight names, and that list
  // had drifted off the contract in both directions: it still checked `idle` and `stale`, which the
  // contract's own comment records as removed, and it never checked `starting` or `misconfigured`.
  // So the one test whose job was "no status renders as unknown" was not looking at two live statuses
  // -- and `starting` missing from a hand-written map is exactly the bug fixed in chat-select.mjs.
  assert.ok(AGENT_STATUSES.length >= 6, "the vocabulary came back empty, so this proves nothing");
  for (const s of AGENT_STATUSES) {
    assert.ok(STATUS_KINDS[s], `STATUS_KINDS must map the contract status '${s}'`);
    assert.equal(resolveStatus(s).kind, s, `'${s}' must resolve to its own kind, not unknown`);
  }
});

test("the retired time-decay names still resolve, as aliases rather than as states", () => {
  // `idle` and `stale` left the vocabulary in 2026-06-18 but stayed in STATUS_KINDS on purpose, so an
  // old row or an older client does not render grey. Asserted as ALIASES, which is what they are --
  // the previous version of this checked them as if they were contract statuses.
  assert.equal(resolveStatus("idle").label, "online", "idle must display as online");
  assert.equal(resolveStatus("stale").label, "offline", "stale must display as offline");
  for (const retired of ["idle", "stale"]) {
    assert.ok(!AGENT_STATUSES.includes(retired), `${retired} is retired and must not be back in the vocabulary`);
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

// ---- the chip escapes its label EXACTLY once, and callers must not help ---------------------------
//
// `renderStatusChip` escapes everything it emits -- tone, kind, why, label, badges -- so a caller that
// escapes a label first gets it escaped twice. Two of the three call sites that pass a `label` did:
// `esc(r.status || 'queued')` and `esc(m.type || ...)`. The third passed it raw, which is correct, so
// the inconsistency was already there to read.
//
// PROVEN, not argued: a label of `a & b` renders as `a &amp;amp; b`, which the operator reads on
// screen as the literal text "a &amp; b".
//
// LATENT, NOT LIVE, and the measurement says so. The live database holds six distinct message types
// (info, response, error, request, review, approval) and three spawn-request statuses (cancelled,
// failed, running); NONE contains a character escaping would change. So this cost nothing today and
// would have cost a garbled chip the first time a vocabulary gained an ampersand.

test("the chip escapes a label exactly once", () => {
  const html = renderStatusChip("queued", { label: "a & b", why: "x" });
  assert.match(html, /a &amp; b/, "the label is not escaped at all, or not the way this asserts");
  assert.doesNotMatch(html, /&amp;amp;/, "the label was escaped twice");
});

test("the chip escapes the parts a caller does not control either", () => {
  // Anti-vacuity for the case above: if `esc` were a no-op the first assertion would still pass on a
  // label that happened to contain the literal text `&amp;`.
  const html = renderStatusChip("queued", { label: "x", why: '"><script>' });
  assert.doesNotMatch(html, /"><script>/, "a hostile `why` reached the attribute unescaped");
  assert.match(html, /&quot;|&gt;|&lt;/, "nothing in the chip was escaped, so this test proves nothing");
});

test("no call site pre-escapes a label into the chip", () => {
  // A SOURCE check, deliberately, because the property is about how the CALL is written: the helper
  // cannot tell an already-escaped label from a legitimate one. The behavioural cases above are the
  // proof that escaping happens; this is the net that stops a caller adding a second layer.
  const dir = path.dirname(fileURLToPath(import.meta.url));
  const offenders = [];
  let callSites = 0;
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".mjs") && !name.endsWith(".js")) continue;
    if (name.includes(".test.")) continue;
    const source = fs.readFileSync(path.join(dir, name), "utf8");
    for (const m of source.matchAll(/renderStatusChip\(/g)) {
      callSites += 1;
      const tail = source.slice(m.index, m.index + 400);
      const label = /\blabel:\s*([^,}]+)/.exec(tail);
      if (label && label[1].includes("esc(")) offenders.push(`${name}: label: ${label[1].trim()}`);
    }
  }
  assert.ok(callSites >= 10, `only ${callSites} call sites found; the scan has drifted`);
  assert.deepEqual(offenders, [], "a caller escapes a label the chip escapes again");
});
