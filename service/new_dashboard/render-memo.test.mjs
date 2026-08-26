// The render memo, tested by CALLING it.
//
// It lived in app.js and was unreachable. The dashboard polls, so every section is asked to re-render on
// a timer whether or not its data moved — and rendering anyway is not merely wasteful: it destroys and
// rebuilds DOM under an operator who may be mid-selection, mid-scroll, or holding a dropdown open. This
// is the guard that stops that, and all of its correctness is in the signature comparison.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  _agentSig,
  _chatChanSig,
  _chatConvSig,
  _contractSig,
  _envSig,
  _msgSig,
  _runSig,
  _spawnReqSig,
  renderSection,
} from "./render-memo.mjs";
import { refreshChipState, resetRefreshHistory } from "./refresh-status.mjs";

/** Distinct keys per test — the signature store is module-global and shared across this file. */
let n = 0;
const key = () => `k${n += 1}`;

test("the FIRST call always renders", () => {
  const k = key();
  let renders = 0;
  renderSection(k, ["a"], () => { renders += 1; });
  assert.equal(renders, 1);
});

test("AN UNCHANGED SIGNATURE SKIPS THE RENDER", () => {
  // The whole point. Without it every poll rebuilds the section's DOM.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a", 1], render);
  renderSection(k, ["a", 1], render);
  renderSection(k, ["a", 1], render);
  assert.equal(renders, 1, "only the first call may render");
});

test("a CHANGED signature renders again", () => {
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a"], render);
  renderSection(k, ["b"], render);
  assert.equal(renders, 2);
});

test("signatures are compared by VALUE, not identity", () => {
  // `JSON.stringify`. A reference comparison would re-render on every poll, since callers build a fresh
  // array each time — which is exactly the bug this memo exists to prevent, hiding as a working memo.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a", { b: 1 }], render);
  renderSection(k, ["a", { b: 1 }], render);
  assert.equal(renders, 1, "an equal-but-not-identical signature must not re-render");
});

test("ORDER IS PART OF THE SIGNATURE", () => {
  // Stringified arrays are order-sensitive, so a reordered list counts as a change. Pinned because it
  // means callers must build their signature deterministically or the memo never holds.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a", "b"], render);
  renderSection(k, ["b", "a"], render);
  assert.equal(renders, 2);
});

test("each KEY is memoised independently", () => {
  // One store for every section. Sharing a slot would make two sections alternately blank each other.
  const a = key();
  const b = key();
  let ra = 0;
  let rb = 0;
  renderSection(a, ["x"], () => { ra += 1; });
  renderSection(b, ["x"], () => { rb += 1; });
  assert.deepEqual([ra, rb], [1, 1], "the same signature under a different key still renders");
  renderSection(a, ["x"], () => { ra += 1; });
  assert.equal(ra, 1);
});

test("undefined and null signatures are distinguishable from each other", () => {
  // `JSON.stringify(undefined)` is undefined and `JSON.stringify(null)` is "null" — different values, so
  // a section swinging between them re-renders rather than sticking on whichever came first.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, null, render);
  renderSection(k, undefined, render);
  assert.equal(renders, 2);
});

test("the signature is recorded BEFORE the render runs", () => {
  // Order matters for re-entrancy: a render that triggers another render of the same section would
  // otherwise recurse. Provoked directly here rather than reasoned about.
  const k = key();
  let renders = 0;
  const render = () => {
    renders += 1;
    if (renders < 5) renderSection(k, ["same"], render);
  };
  assert.doesNotThrow(() => renderSection(k, ["same"], render));
  assert.equal(renders, 1, "the re-entrant call must be memoised out");
});

// --- the signature builders ---------------------------------------------------------------------
//
// These ARE the memo's correctness. Each names the fields whose change should repaint a section, and the
// two failure directions are invisible to `renderSection` itself: a field left out makes that section go
// BLIND to a real update — an agent goes offline and the rail keeps showing it live — while an unstable
// value makes it repaint on every poll, which is the DOM-churn the memo exists to prevent.

const COLLECTIONS = ["agents", "contracts", "runs", "environments", "spawnRequests", "messages", "chat"];

function withData(fields, run) {
  const saved = {};
  for (const k of COLLECTIONS) saved[k] = state[k];
  Object.assign(state, fields);
  try { return run(); } finally { Object.assign(state, saved); }
}

test("EVERY builder is STABLE across calls on unchanged data", () => {
  // The property the memo depends on. Anything non-deterministic here — a timestamp, an object
  // identity, an unsorted Set — makes the signature differ every poll and the memo never holds.
  withData({
    agents: [{ id: "a", status: "online" }],
    contracts: [{ id: "c", state: "open", status: "x", overdue: false, subject: "s" }],
    runs: [{ id: "r", status: "queued", subject: "s", summary: "y", targetAgentId: "a" }],
    environments: [{ id: "e", status: "online", label: "L" }],
    spawnRequests: [{ id: "sr", status: "queued", agentId: "a", error: "", updatedAt: 1 }],
    messages: [{ id: "m", from: "a", subject: "s", read: false }],
    chat: { channels: [{ name: "general", unreadCount: 0, memberCount: 3 }] },
  }, () => {
    for (const [name, fn] of Object.entries({
      _agentSig, _contractSig, _runSig, _envSig, _spawnReqSig, _msgSig, _chatChanSig,
    })) {
      assert.deepEqual(fn(), fn(), `${name} must be stable`);
      assert.equal(JSON.stringify(fn()), JSON.stringify(fn()), `${name} must stringify identically`);
    }
  });
});

test("a STATUS change moves the agent signature — the rail must not go blind to it", () => {
  // The single most important field in the set: an agent going offline while the rail still shows it
  // live is the failure an operator acts on wrongly.
  withData({ agents: [{ id: "a", status: "online" }] }, () => {
    const before = JSON.stringify(_agentSig());
    state.agents = [{ id: "a", status: "offline" }];
    assert.notEqual(JSON.stringify(_agentSig()), before);
  });
});

test("each builder responds to every field it names", () => {
  // Systematic rather than spot-checked: flip one field at a time and require the signature to move.
  // A field listed but not actually read would otherwise sit there looking like coverage.
  const cases = [
    [_contractSig, "contracts", { id: "c", state: "open", status: "x", overdue: false, subject: "s" },
      ["state", "status", "overdue", "subject"]],
    [_runSig, "runs", { id: "r", status: "queued", subject: "s", summary: "y", targetAgentId: "a" },
      ["status", "subject", "summary", "targetAgentId"]],
    [_envSig, "environments", { id: "e", status: "online", label: "L" }, ["status", "label"]],
    [_spawnReqSig, "spawnRequests", { id: "sr", status: "queued", agentId: "a", error: "", updatedAt: 1 },
      ["status", "agentId", "error", "updatedAt"]],
    [_msgSig, "messages", { id: "m", from: "a", subject: "s", read: false }, ["from", "subject", "read"]],
  ];
  for (const [fn, key, record, fields] of cases) {
    withData({ [key]: [record] }, () => {
      const before = JSON.stringify(fn());
      for (const field of fields) {
        state[key] = [{ ...record, [field]: "CHANGED" }];
        assert.notEqual(JSON.stringify(fn()), before, `${key}.${field} must move the signature`);
      }
    });
  }
});

test("_runSig reads EITHER spelling of the target agent", () => {
  // `r.targetAgentId || r.target_agent`. The API has returned both; reading one means a reassignment
  // arriving in the other spelling never repaints the run list.
  withData({ runs: [{ id: "r", target_agent: "a1" }] }, () => {
    const before = JSON.stringify(_runSig());
    state.runs = [{ id: "r", target_agent: "a2" }];
    assert.notEqual(JSON.stringify(_runSig()), before, "snake_case must be read too");
  });
});

test("_chatChanSig survives channels being absent", () => {
  // `(state.chat.channels || [])`. Channels are undefined until the first chat load, and this runs on
  // every render pass from boot.
  withData({ chat: {} }, () => {
    assert.doesNotThrow(() => _chatChanSig());
    assert.deepEqual(_chatChanSig(), []);
  });
});

test("an EMPTY collection yields an empty signature, not a throw", () => {
  withData({
    agents: [], contracts: [], runs: [], environments: [], spawnRequests: [], messages: [],
    chat: { channels: [] },
  }, () => {
    for (const fn of [_agentSig, _contractSig, _runSig, _envSig, _spawnReqSig, _msgSig, _chatChanSig]) {
      assert.deepEqual(fn(), []);
    }
  });
});

test("_chatConvSig moves when a conversation gains a message, and survives no channelMessages", () => {
  // The seventh builder, and the only one keyed on a MAP rather than a list. It reports each
  // conversation's message COUNT, which is what makes a new message repaint the chat pane — without it
  // the pane holds its last render until something else in the signature happens to change.
  const saved = state.chat;
  try {
    state.chat = { channelMessages: { general: [{ id: "m1" }] } };
    const before = JSON.stringify(_chatConvSig());
    state.chat = { channelMessages: { general: [{ id: "m1" }, { id: "m2" }] } };
    assert.notEqual(JSON.stringify(_chatConvSig()), before, "a new message must move the signature");

    state.chat = {};
    assert.doesNotThrow(() => _chatConvSig());
    assert.deepEqual(_chatConvSig(), [], "absent channelMessages is empty, not a throw");
  } finally {
    state.chat = saved;
  }
});

// ── a section that throws must not take the others, or itself, down ─────────────────────────────
// TWO THINGS WERE WRONG, and both only appear on a render that throws.
//
// The signature was recorded BEFORE the render ran, so a throwing renderer left the memo saying
// "this state is already drawn" for a state that never was -- and the section stayed blank until its
// data changed AGAIN, because the next cycle compared equal and returned early.
//
// And there was no try/catch, in a loop of ELEVEN sections called in order. A throw in section 2
// meant sections 3 to 11 never rendered that cycle. `renderAttention` carries the comment "never let
// a missing node throw out of the unconditional renderAll loop"; measured across the loop, 7 of 11
// guard their host node and 3 do not. Guarding those three fixes the missing-node case only; this
// covers every way a render can throw.

test("a throwing section does NOT record its signature, so the next cycle retries it", () => {
  const k = key();
  let attempts = 0;
  const flaky = () => { attempts += 1; if (attempts === 1) throw new Error("bad frame"); };

  renderSection(k, ["same"], flaky);
  assert.equal(attempts, 1, "the first render must be attempted");

  // SAME signature. Before the fix this returned early forever and the panel stayed blank.
  renderSection(k, ["same"], flaky);
  assert.equal(attempts, 2, "an identical signature was treated as already drawn after a failure");
});

test("and once it succeeds, the memo goes back to skipping", () => {
  // The retry must not become a permanent re-render: that would defeat the memo for any section that
  // ever hiccupped, repainting DOM under the operator on every poll.
  const k = key();
  let attempts = 0;
  const flaky = () => { attempts += 1; if (attempts === 1) throw new Error("bad frame"); };

  renderSection(k, ["same"], flaky);
  renderSection(k, ["same"], flaky);
  assert.equal(attempts, 2);
  renderSection(k, ["same"], flaky);
  assert.equal(attempts, 2, "a recovered section must be memoised again");
});

test("a throwing section does not stop the NEXT section rendering", () => {
  // The loop calls eleven of these in order. This is the whole blast radius.
  const bad = key();
  const good = key();
  let painted = 0;
  const renderAll = () => {
    renderSection(bad, ["x"], () => { throw new Error("boom"); });
    renderSection(good, ["y"], () => { painted += 1; });
  };
  assert.doesNotThrow(renderAll, "a section's failure escaped the loop");
  assert.equal(painted, 1, "the section after the failure never rendered");
});

test("the failure is REPORTED, not swallowed", () => {
  // A silently blank panel is the failure this repo keeps finding. The out-of-band list is what the
  // connection chip drains, so a render failure has to reach it under a name that says WHICH section
  // -- "something did not draw" sends an operator nowhere.
  const k = key();
  resetRefreshHistory();
  renderSection(k, ["x"], () => { throw new Error("boom"); });
  const chip = refreshChipState([]);
  assert.ok(
    chip.failed.includes(`render:${k}`),
    `the failed section was not reported; chip reported ${chip.failed.join(", ") || "nothing"}`,
  );
});

test("a successful render reports nothing", () => {
  const k = key();
  resetRefreshHistory();
  renderSection(k, ["x"], () => {});
  assert.deepEqual(refreshChipState([]).failed, []);
});

test("a section that throws EVERY time is retried every time", () => {
  // Noisy and correct. The alternative is the latch this replaces: one bad frame and the panel is
  // dark until unrelated data happens to move.
  const k = key();
  let attempts = 0;
  for (let i = 0; i < 4; i += 1) {
    renderSection(k, ["same"], () => { attempts += 1; throw new Error("always"); });
  }
  assert.equal(attempts, 4);
});
