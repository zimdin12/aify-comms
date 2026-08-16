#!/usr/bin/env node
// Every codex app-server notification, and what it does to the turn's state.
//
// `buildCodexNotificationHandler` is the whole read side of a legacy codex turn: it accumulates the
// answer text, names the active turn, tracks in-flight items, records the terminal status, and
// decides — via `ctx.settled` — whether the turn has finished at all. No test called it.
//
// It is dependency-injected and free of platform gates, filesystem and network: `{ ctx,
// pushTerminalFrame, markActivity }` in, mutations and callbacks out. So this is the
// doctor-predicates pattern the JS standard names — a real unit test that CALLS the thing, rather
// than a source scan asserting a line was written.
//
// WHAT `ctx.settled` COSTS IF IT IS WRONG. The controller resolves the turn only once settled is
// true; otherwise the run waits on the absolute / quiet-stall timers. `finalStatus` starts as
// "failed", so a turn that never settles reports failure after a timeout rather than immediately.
// That makes the settle rules the highest-value assertions here, and the reason the unlisted-status
// case below is pinned rather than left to be discovered.

import assert from "node:assert/strict";

import { buildCodexNotificationHandler } from "../controllers/codex-legacy-helpers.js";

/** A ctx shaped exactly like the controller's (codex-legacy-controller.js), plus recorders. */
function harness() {
  const events = [];
  const refs = [];
  const frames = [];
  const activity = [];
  const ctx = {
    finalText: "",
    finalStatus: "failed",
    finalError: "",
    activeTurnId: null,
    settled: false,
    activeItems: new Map(),
    callbacks: {
      onEvent: (kind, text) => events.push([kind, text]),
      onRefs: (r) => refs.push(r),
    },
  };
  const handle = buildCodexNotificationHandler({
    ctx,
    pushTerminalFrame: (f) => frames.push(f),
    markActivity: (label) => activity.push(label),
  });
  return { ctx, handle, events, refs, frames, activity };
}

// ── turn lifecycle ───────────────────────────────────────────────────────────────────────────
{
  const h = harness();
  h.handle({ method: "turn/started", params: { turn: { id: "t1" } } });
  assert.equal(h.ctx.activeTurnId, "t1");
  assert.deepEqual(h.refs, [{ turnId: "t1" }], "interrupt()/steer() read activeTurnId via onRefs");
  assert.deepEqual(h.events, [["turn", "Started turn t1"]]);
  assert.equal(h.frames.length, 1);
  assert.equal(h.ctx.settled, false, "a started turn is not a finished one");
}
{
  // A turn/started with no id matches NO branch — the chain is else-if and the first arm requires
  // params.turn.id. Pinned so the silence is a decision, not a surprise.
  const h = harness();
  h.handle({ method: "turn/started", params: { turn: {} } });
  assert.equal(h.ctx.activeTurnId, null);
  assert.deepEqual(h.refs, []);
  assert.deepEqual(h.activity, ["turn/started"], "activity is still marked — the socket is alive");
}

// ── settle rules: the ones that decide whether a run resolves or waits for a timer ───────────
for (const status of ["completed", "interrupted", "failed"]) {
  const h = harness();
  h.handle({ method: "turn/completed", params: { turn: { status } } });
  assert.equal(h.ctx.finalStatus, status);
  assert.equal(h.ctx.settled, true, `${status} is terminal and must settle the turn`);
}
{
  const h = harness();
  h.handle({ method: "turn/completed", params: { turn: {} } });
  assert.equal(h.ctx.finalStatus, "completed", "a missing status defaults to completed");
  assert.equal(h.ctx.settled, true, "...and therefore settles");
}
{
  // PINNED, NOT RULED. A terminal status outside the three listed leaves `settled` false, so the
  // controller falls through to its timers instead of resolving. That is a slow failure rather than
  // a stuck one — the timers exist — but it is worth knowing before someone adds a status upstream.
  const h = harness();
  h.handle({ method: "turn/completed", params: { turn: { status: "cancelled" } } });
  assert.equal(h.ctx.finalStatus, "cancelled");
  assert.equal(
    h.ctx.settled, false,
    "an unlisted status does not settle. If codex starts emitting one, the turn waits for the "
      + "absolute/quiet timer and reports whatever finalStatus holds — add it to the settle set",
  );
}
{
  const h = harness();
  h.handle({
    method: "turn/completed",
    params: { turn: { status: "failed", error: { message: "boom" } } },
  });
  assert.equal(h.ctx.finalError, "boom", "the turn's own error must reach the caller");
}

// ── usage rendering ──────────────────────────────────────────────────────────────────────────
{
  const h = harness();
  h.handle({
    method: "turn/completed",
    params: { turn: { status: "completed", usage: { input_tokens: 12, output_tokens: 34 } } },
  });
  assert.match(h.frames.join(""), /in=12 out=34/);
}
{
  const h = harness();
  h.handle({ method: "turn/completed", params: { usage: { input_tokens: 7 } } });
  assert.match(h.frames.join(""), /in=7 out=0/, "usage may arrive beside the turn, not inside it");
}
{
  const h = harness();
  h.handle({ method: "turn/completed", params: { turn: { status: "completed", usage: {} } } });
  assert.doesNotMatch(h.frames.join(""), /in=/, "an all-zero usage block prints no counts");
}

// ── the answer text ──────────────────────────────────────────────────────────────────────────
{
  const h = harness();
  h.handle({ method: "item/agentMessage/delta", params: { delta: "Hel" } });
  h.handle({ method: "item/agentMessage/delta", params: { delta: "lo" } });
  assert.equal(h.ctx.finalText, "Hello", "deltas accumulate — this IS the reply");
  assert.deepEqual(h.frames, ["Hel", "lo"], "and stream to the terminal verbatim");
}
{
  const h = harness();
  h.handle({ method: "item/agentMessage/delta", params: { delta: "" } });
  assert.equal(h.ctx.finalText, "");
  assert.deepEqual(h.frames, [], "an empty delta must not push an empty frame");
}
{
  // A completed agentMessage REPLACES the accumulated text: the item carries the authoritative
  // final string, and the deltas were a preview of it.
  const h = harness();
  h.handle({ method: "item/agentMessage/delta", params: { delta: "partial" } });
  h.handle({
    method: "item/completed",
    params: { item: { id: "i1", type: "agentMessage", text: "the whole answer" } },
  });
  assert.equal(h.ctx.finalText, "the whole answer");
}
{
  const h = harness();
  h.handle({ method: "item/agentMessage/delta", params: { delta: "streamed" } });
  h.handle({ method: "item/completed", params: { item: { id: "i1", type: "agentMessage" } } });
  assert.equal(h.ctx.finalText, "streamed", "a completed item with no text must not erase the deltas");
}

// ── in-flight item tracking ──────────────────────────────────────────────────────────────────
{
  // The start payload is RICH and the completion THIN, which is the case that distinguishes the two
  // possible label sources. A first version used identical payloads for both, so `describeCodexItem`
  // and the remembered label produced the same string and the assertion could not tell them apart —
  // a mutation that dropped the remembered label passed. The difference is not cosmetic:
  // `isAifyCommsMcpToolItem` matches a label containing BOTH "mcpToolCall" AND "aify-comms", so a
  // completion relabelled from the thin payload loses the server and stops being recognised as an
  // aify-comms tool call — which is what the MCP-tool stall timer keys on.
  const h = harness();
  h.handle({
    method: "item/started",
    params: { item: { id: "i1", type: "mcpToolCall", server: "aify-comms", name: "comms_send" } },
  });
  assert.equal(h.ctx.activeItems.size, 1);
  const [kind, text] = h.events.at(-1);
  assert.equal(kind, "codex");
  assert.equal(text, "Started mcpToolCall aify-comms/comms_send");

  const label = h.ctx.activeItems.get("i1").label;
  h.handle({ method: "item/completed", params: { item: { id: "i1", type: "mcpToolCall" } } });
  assert.equal(h.ctx.activeItems.size, 0, "a completed item must not stay in-flight");
  assert.deepEqual(
    h.events.at(-1), ["codex", `Completed ${label}`],
    "the completion must be labelled from the REMEMBERED start label, so it matches the Started "
      + "line even when the completed payload carries less",
  );
  assert.match(h.events.at(-1)[1], /aify-comms/, "the server must survive into the completion label");
}
{
  // Completion without a matching start still names the item, from the payload.
  const h = harness();
  h.handle({ method: "item/completed", params: { item: { id: "orphan", type: "commandExecution" } } });
  assert.equal(h.events.length, 1);
  assert.match(h.events[0][1], /^Completed /);
}
{
  // ASYMMETRY, PINNED: a completed agentMessage is handled by the earlier arm, which deletes the
  // item but emits NO "Completed" event and no frame — the answer text is the output, and a chrome
  // line would sit in the middle of it.
  const h = harness();
  h.handle({ method: "item/started", params: { item: { id: "i1", type: "agentMessage" } } });
  const startedEvents = h.events.length;
  h.handle({ method: "item/completed", params: { item: { id: "i1", type: "agentMessage" } } });
  assert.equal(h.ctx.activeItems.size, 0, "it is still cleared from in-flight");
  assert.equal(h.events.length, startedEvents, "but no Completed event is emitted for it");
}

// ── transport errors ─────────────────────────────────────────────────────────────────────────
{
  const h = harness();
  h.handle({ method: "error", params: { error: { message: "rpc exploded" } } });
  assert.equal(h.ctx.finalError, "rpc exploded");
  assert.match(h.frames.join(""), /rpc exploded/);
  assert.equal(h.ctx.settled, false, "an error notification does not by itself end the turn");
}
{
  const h = harness();
  h.handle({ method: "error", params: { error: {} } });
  assert.equal(h.ctx.finalError, "", "an error with no message must not blank-out a real one");
}

// ── robustness of the shapes it is handed ────────────────────────────────────────────────────
{
  const h = harness();
  h.handle({ method: "something/unknown", params: { whatever: 1 } });
  assert.deepEqual(h.activity, ["something/unknown"]);
  assert.deepEqual(h.events, []);
  assert.deepEqual(h.frames, []);
  assert.equal(h.ctx.settled, false);
}
{
  const h = harness();
  h.handle({});
  assert.deepEqual(h.activity, ["runtime notification"], "a method-less message still marks activity");
}
{
  // The callbacks are optional at every call site (`?.`), and a controller may supply neither.
  const events = [];
  const ctx = {
    finalText: "", finalStatus: "failed", finalError: "", activeTurnId: null,
    settled: false, activeItems: new Map(), callbacks: {},
  };
  const handle = buildCodexNotificationHandler({
    ctx, pushTerminalFrame: () => {}, markActivity: () => events.push(1),
  });
  handle({ method: "turn/started", params: { turn: { id: "t9" } } });
  handle({ method: "item/started", params: { item: { id: "i9", type: "commandExecution" } } });
  assert.equal(ctx.activeTurnId, "t9", "state must still advance with no callbacks attached");
  assert.equal(ctx.activeItems.size, 1);
}

console.log("codex-notification-handler.test.js: all assertions passed");
