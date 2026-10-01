#!/usr/bin/env node
// startSessionEffort sets a hermes agent's reasoning effort on each live session of its gateway, once per
// session, through `config.set {key: "reasoning", value, session_id}` (P0 C9; review of P6, R1). What the
// gateway does with that call was asked of a sealed host (docs/superpowers/plans/evidence/2026-10-01-p6/
// hermes-model-probe.mjs); this pins what the loop sends, and when.
import assert from "node:assert/strict";
import { startSessionEffort } from "../hermes-session-effort.mjs";

const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const live = (...ids) => ({ sessions: ids.map((id, i) => ({ id, started_at: new Date(Date.UTC(2026, 9, 1, 0, i)).toISOString() })) });

/** A gateway whose live sessions and config.set answers the test controls; every request is recorded. */
function gateway({ sessions = live("s1"), refuse = () => null } = {}) {
  const state = { sessions, sent: [], logs: [] };
  const openWs = async () => ({
    request: async (frame) => {
      state.sent.push(frame);
      if (frame.method === "session.active_list") return state.sessions;
      const refusal = refuse(frame);
      if (refusal) throw refusal;
      return { key: "reasoning", value: frame.params.value };
    },
    close() {},
  });
  const start = (opts = {}) => startSessionEffort({ agentId: "hermes-lead", effort: "high", intervalMs: 20, tempDir: "/tmp", openWs,
    readGatewayUrl: () => ({ gatewayUrl: "ws://127.0.0.1:1/api/ws?token=secret" }), log: (m) => state.logs.push(m), ...opts });
  const sets = () => state.sent.filter((f) => f.method === "config.set").map((f) => f.params);
  return { state, start, sets };
}

// (1) ONCE PER SESSION: the live session gets the effort, and the next passes leave it alone.
{
  const g = gateway();
  const stop = g.start();
  await wait(120);
  stop();
  assert.deepEqual(g.sets(), [{ key: "reasoning", value: "high", session_id: "s1" }]);
  assert.ok(g.state.sent.filter((f) => f.method === "session.active_list").length >= 3, "positive control: it kept looking");
}

// (2) A NEW LIVE SESSION (the TUI relaunched, or a fresh context) gets it too.
{
  const g = gateway();
  const stop = g.start();
  await wait(60);
  g.state.sessions = live("s1", "s2");
  await wait(80);
  stop();
  assert.deepEqual(g.sets().map((p) => p.session_id), ["s1", "s2"]);
}

// (3) A REFUSAL is retried on the next pass, said once, and a token never reaches the log.
{
  let refusals = 2;
  const g = gateway({ refuse: () => (refusals-- > 0 ? { code: 4001, message: "session not found ws://x?token=abc" } : null) });
  const stop = g.start();
  await wait(150);
  stop();
  assert.deepEqual(g.sets().map((p) => p.session_id), ["s1", "s1", "s1"], "retried until it was taken, then left alone");
  const refused = g.state.logs.filter((m) => m.includes("not yet set"));
  assert.equal(refused.length, 1, g.state.logs.join("\n"));
  assert.ok(!refused[0].includes("abc"), refused[0]);
  assert.equal(g.state.logs.filter((m) => m.includes("set on session s1")).length, 1);
}

// (4) NOTHING TO SET, OR NO ONE TO SET IT FOR: no request at all, and the stop is still callable.
{
  for (const opts of [{ effort: "" }, { effort: "  " }, { agentId: "" }, { intervalMs: 0 }]) {
    const g = gateway();
    const stop = g.start(opts);
    await wait(50);
    stop();
    stop();
    assert.deepEqual(g.state.sent, [], JSON.stringify(opts));
  }
}

// (5) NO GATEWAY YET, or one with no live session: nothing is set, and it keeps looking.
{
  const none = gateway();
  const stop = none.start({ readGatewayUrl: () => null });
  await wait(60);
  stop();
  assert.deepEqual(none.state.sent, []);
  const empty = gateway({ sessions: live() });
  const stop2 = empty.start();
  await wait(60);
  stop2();
  assert.deepEqual(empty.sets(), []);
}

console.log("hermes-session-effort: ok");
