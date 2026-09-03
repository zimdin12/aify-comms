// The dashboard and the doctor must answer "who shares this session" the same way.
//
// WHY TWO IMPLEMENTATIONS EXIST AT ALL. `mcp/stdio/session-handle-check.mjs` owns this question for
// `aify-comms doctor`; it is a host-side bridge module and cannot be imported into a browser bundle.
// The alternative to a second copy is the dashboard not answering the question -- which is exactly
// the operator's complaint ("some random path for aify-comms doctor... no. will never use it").
//
// SO THE COPY IS DELIBERATE AND THIS FILE IS WHAT KEEPS IT HONEST: both are driven over one corpus
// and any disagreement fails. This repo's standing answer to duplication it cannot remove -- the
// same treatment `credential-ref.mjs` gets for aify-env's grammar, and for the same reason: the two
// agree until one is edited, and nothing else would notice.
//
// THE TWO SHAPES ARE DIFFERENT ON PURPOSE. The doctor reports the fleet-wide list of collisions;
// the drawer reports the OTHER agents sharing ONE agent's session. Same fact, two questions -- so
// the agreement is derived rather than compared field by field: for every agent, the drawer's answer
// must equal what the doctor's collision list implies for that agent.

import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { sessionSharers } from "./agent-session-sharing.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DOCTOR = path.join(HERE, "..", "..", "mcp", "stdio", "session-handle-check.mjs");

const { duplicateSessionHandles } = await import(`file://${DOCTOR.split("\\").join("/")}`);

/** What the DOCTOR's fleet-wide list implies for one agent: its collision partners. */
function sharersFromDoctor(agents, agentId) {
  for (const row of duplicateSessionHandles(agents)) {
    if (row.agentIds.includes(agentId)) {
      return row.agentIds.filter((id) => id !== agentId).sort();
    }
  }
  return [];
}

/** Every shape worth disagreeing about, including the ones only one side would get wrong. */
const CORPUS = [
  { name: "nothing at all", agents: {} },
  { name: "one agent, no handle", agents: { a: {} } },
  { name: "one agent with a handle", agents: { a: { sessionHandle: "h1" } } },
  {
    name: "the live shape: three collisions across eight agents",
    agents: {
      "guns-ab-planner": { sessionHandle: "20260715_001441_960b8f" },
      "mc-senior-dev": { sessionHandle: "20260715_001441_960b8f" },
      "pathweaver-activation-auditor": { sessionHandle: "20260715_001441_960b8f" },
      "safety-gate-auditor": { sessionHandle: "20260715_001441_960b8f" },
      "comms-claude": { sessionHandle: "651b895f-a564-4d3a-8e0b-27f8429b1dd0" },
      "comms-tech-lead": { sessionHandle: "651b895f-a564-4d3a-8e0b-27f8429b1dd0" },
      "mrg-agent": { sessionHandle: "8c106750-de4f-481c-b7d5-aa02d2b27273" },
      "pc-manager": { sessionHandle: "8c106750-de4f-481c-b7d5-aa02d2b27273" },
      "alone": { sessionHandle: "solo" },
      "handleless": {},
    },
  },
  {
    name: "MANY agents with EMPTY handles, which must not group together",
    agents: {
      a: { sessionHandle: "" }, b: { sessionHandle: "   " }, c: {},
      d: { sessionHandle: null }, e: { sessionHandle: "real" },
    },
  },
  {
    name: "whitespace around a handle -- same session or not?",
    agents: { a: { sessionHandle: "h1" }, b: { sessionHandle: " h1 " } },
  },
  {
    name: "a malformed row beside a real one",
    agents: { a: null, b: { sessionHandle: "h1" }, c: { sessionHandle: "h1" } },
  },
];

test("THE CORPUS ACTUALLY EXERCISES A COLLISION", () => {
  // POSITIVE CONTROL. Every assertion below is an equality between two functions, and two functions
  // that both return [] for everything agree perfectly while proving nothing.
  const live = CORPUS.find((c) => c.name.startsWith("the live shape")).agents;
  assert.equal(duplicateSessionHandles(live).length, 3, "the doctor found no collisions in the live shape");
  assert.deepEqual(sessionSharers(live, "comms-claude"), ["comms-tech-lead"]);
  assert.equal(sessionSharers(live, "alone").length, 0, "an agent with its own session reported sharers");
});

test("THE DRAWER AND THE DOCTOR AGREE, for every agent in every shape", () => {
  for (const { name, agents } of CORPUS) {
    for (const agentId of Object.keys(agents)) {
      assert.deepEqual(
        sessionSharers(agents, agentId),
        sharersFromDoctor(agents, agentId),
        `disagreement on "${agentId}" in: ${name}`,
      );
    }
  }
});

test("and they agree on an agent that is not in the population at all", () => {
  // A drawer can outlive a poll: the operator opens an agent, it is removed, the panel refills.
  for (const { name, agents } of CORPUS) {
    assert.deepEqual(sessionSharers(agents, "never-registered"), sharersFromDoctor(agents, "never-registered"),
      `disagreement on a missing agent in: ${name}`);
  }
});

test("the LIST shape agrees with the MAP shape", () => {
  // `/agents` returns a map and `state.agents` is an array, so the dashboard side must accept both
  // and answer identically. The doctor only ever sees the map.
  const map = CORPUS.find((c) => c.name.startsWith("the live shape")).agents;
  const list = Object.entries(map).map(([id, agent]) => ({ id, ...(agent || {}) }));
  for (const agentId of Object.keys(map)) {
    assert.deepEqual(sessionSharers(list, agentId), sessionSharers(map, agentId),
      `list and map disagree on "${agentId}"`);
  }
});
