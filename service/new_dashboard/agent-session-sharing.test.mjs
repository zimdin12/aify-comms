// B4: the doctor's `session-handles` check, where the operator will actually see it.
//
// "i never go to that path... i have container that should give me that info. some random path for
// aify-comms doctor... no. will never use it." The dashboard already polls `/agents` -- the whole
// population -- so this costs no endpoint, no extra poll and no service change. The data was on the
// page, unread.
//
// LIVE ON THIS HOST while this was written: three handles claimed by eight agents, one of them the
// very conversation that built it (`comms-claude` and `comms-tech-lead`). The fleet-wide form is the
// doctor's; the per-agent form is the one that explains a symptom somebody is looking at.

import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_SHARING_ID, renderSessionSharing, sessionSharers } from "./agent-session-sharing.mjs";

const LIVE = {
  "comms-claude": { sessionHandle: "651b895f-a564-4d3a-8e0b-27f8429b1dd0" },
  "comms-tech-lead": { sessionHandle: "651b895f-a564-4d3a-8e0b-27f8429b1dd0" },
  "mc-senior-dev": { sessionHandle: "20260715_001441_960b8f" },
  "guns-ab-planner": { sessionHandle: "20260715_001441_960b8f" },
  "safety-gate-auditor": { sessionHandle: "20260715_001441_960b8f" },
  alone: { sessionHandle: "solo" },
  handleless: {},
};

test("IT NAMES THE OTHER AGENTS CLAIMING THIS SESSION", () => {
  // POSITIVE CONTROL for everything below, which is mostly about staying silent.
  const html = renderSessionSharing(LIVE, "comms-claude");
  assert.match(html, /comms-tech-lead/);
  assert.match(html, /1 other agent\b/, "the count is wrong or absent");
  assert.ok(!/comms-claude/.test(html), "it listed the agent whose drawer this is");
});

test("it counts and lists ALL of them, not just the first", () => {
  const html = renderSessionSharing(LIVE, "mc-senior-dev");
  assert.match(html, /2 other agents/);
  assert.match(html, /guns-ab-planner/);
  assert.match(html, /safety-gate-auditor/);
});

test("SILENT WHEN THE SESSION IS THIS AGENT'S ALONE", () => {
  // 36 of 44 agents on this host are healthy. A row reading "belongs to this agent alone" on every
  // one of them is noise that teaches the reader to skip the panel -- and the eight that matter get
  // skipped with it.
  assert.equal(renderSessionSharing(LIVE, "alone"), "");
});

test("SILENT FOR AN AGENT WITH NO HANDLE, which is not the same as sharing", () => {
  // 11 of 44 carry no handle. Grouping those together would report the healthy majority as one
  // enormous collision, which is the mistake that gets a check like this switched off.
  assert.equal(renderSessionSharing(LIVE, "handleless"), "");
  assert.deepEqual(sessionSharers(LIVE, "handleless"), []);
});

test("EMPTY AND WHITESPACE HANDLES DO NOT COLLIDE WITH EACH OTHER", () => {
  const agents = {
    a: { sessionHandle: "" }, b: { sessionHandle: "   " }, c: {}, d: { sessionHandle: null },
  };
  for (const id of Object.keys(agents)) {
    assert.equal(renderSessionSharing(agents, id), "", `${id} reported sharing an empty handle`);
  }
});

test("a handle with surrounding whitespace IS the same session", () => {
  // The two sides of one conversation, written by different writers. Trimming is what makes them
  // one, and the agreement test pins that the doctor trims too.
  const agents = { a: { sessionHandle: "h1" }, b: { sessionHandle: " h1 " } };
  assert.deepEqual(sessionSharers(agents, "a"), ["b"]);
});

test("a blank agent id reports nothing rather than everything", () => {
  // FAILS CLOSED. An empty id that matched the first handleless row would put unrelated agents in
  // somebody's drawer.
  assert.deepEqual(sessionSharers(LIVE, ""), []);
  assert.deepEqual(sessionSharers(LIVE, null), []);
});

test("an agent id is ESCAPED, because it is host-authored text in a page", () => {
  const agents = {
    victim: { sessionHandle: "shared" },
    '<img src=x onerror="alert(1)">': { sessionHandle: "shared" },
  };
  const html = renderSessionSharing(agents, "victim");
  assert.ok(!/<img/.test(html), "an agent id was rendered as markup");
  assert.match(html, /&lt;img/);
});

test("it accepts the ARRAY shape state.agents actually holds", () => {
  // `/agents` returns a map; `state.agents` is an array of rows carrying `id`. A function that only
  // understood one would silently answer "no sharing" for the whole fleet.
  const list = Object.entries(LIVE).map(([id, agent]) => ({ id, ...agent }));
  assert.deepEqual(sessionSharers(list, "comms-claude"), ["comms-tech-lead"]);
});

test("a missing or malformed population is not an error", () => {
  for (const agents of [undefined, null, [], {}, [null], [{}]]) {
    assert.equal(renderSessionSharing(agents, "comms-claude"), "");
  }
});

test("the container id is shared, not spelled twice", () => {
  assert.equal(typeof AGENT_SHARING_ID, "string");
  assert.ok(AGENT_SHARING_ID.length > 0);
});
