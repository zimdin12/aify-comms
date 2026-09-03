// The `tools/list` payload may only get smaller.
//
// WHY A RATCHET AND NOT A CAP. This surface is always-loaded context: 30 tools, 14,491 characters of
// description and field text, re-read by every agent on every turn for the life of the fleet. It is
// the same argument `skill-size-ratchet.test.js` already makes for `SKILL.md`, on a surface nobody
// was measuring -- and nothing here had a ceiling until 2026-09-03, so the only pressure on it was
// whoever last remembered that agents pay for it.
//
// RAISING A CEILING IS A DECISION, NOT A REPAIR. If a tool genuinely needs more, say in the commit
// what the agent gains and pay for it elsewhere. Nudging a number to clear a red test is exactly the
// move this gate exists to catch, and it is cheap to make and invisible afterwards.
//
// A NEW TOOL WITH NO CEILING FAILS. Otherwise the gate governs only what somebody remembered to add,
// which is the shape that let this surface grow unmeasured in the first place.
//
// THE NUMBERS COME FROM THE MEASUREMENT, not from a reading of the source. `tool-surface-size.mjs`
// parses the registrations (descriptions AND every `.describe()` on the schema, because on several
// tools the schema half is the larger one) and this file only compares. A gate whose number is typed
// by hand is a gate that disagrees with the thing it measures.

import { test } from "node:test";
import assert from "node:assert/strict";

import { measureToolSurface } from "./tool-surface-size.mjs";

/** Measured 2026-09-03. Characters of description + schema text, per tool. May only go DOWN. */
const CEILINGS = {
  comms_agent_info: 145,
  comms_agents: 67,
  comms_channel_create: 164,
  comms_channel_join: 128,
  comms_channel_leave: 293,
  comms_clear: 698,
  comms_compact: 1332,
  comms_console_input: 939,
  comms_console_tail: 476,
  comms_contracts: 479,
  comms_delete_session: 176,
  comms_describe: 346,
  comms_dispatch: 1046,
  comms_envs: 324,
  comms_files: 194,
  comms_inbox: 552,
  comms_interrupt: 272,
  comms_listen: 348,
  comms_read: 52,
  comms_remove_agent: 496,
  comms_restart: 829,
  comms_run_interrupt: 166,
  comms_run_status: 104,
  comms_send: 2532,
  comms_share: 305,
  comms_spawn: 790,
  comms_status: 800,
  comms_unsend: 164,
  comms_unshare: 116,
  comms_usage: 190,
};

test("THE MEASUREMENT FINDS THE TOOLS AT ALL", () => {
  // POSITIVE CONTROL. Every assertion below is satisfied by an empty measurement -- no tool is over
  // its ceiling when there are no tools -- so the gate would pass loudest exactly when its parser
  // had broken.
  const tools = measureToolSurface();
  assert.ok(tools.length >= 30, `only ${tools.length} tools found; the parser is not reaching them`);
  const send = tools.find((t) => t.name === "comms_send");
  assert.ok(send && send.description > 0,
    "comms_send's description measured as zero -- it is declared as a CONSTANT, and a parser that "
    + "only understands inline literals reports the biggest description in the tree as free");
  assert.ok(tools.some((t) => t.schema > 0), "no schema text was measured on any tool");
});

test("NO TOOL IS OVER ITS CEILING", () => {
  const over = measureToolSurface()
    .filter((t) => CEILINGS[t.name] !== undefined && t.total > CEILINGS[t.name])
    .map((t) => `${t.name}: ${t.total} > ${CEILINGS[t.name]}`);
  assert.deepEqual(over, [],
    "a tool description grew. Every agent re-reads this on every turn, so the question is not "
    + "whether the new sentence is true but whether it changes what the CALLER does. If it earns "
    + "its place, take the room from somewhere else in the same tool.");
});

test("every tool HAS a ceiling", () => {
  // A new tool arriving ungoverned is how the surface grew unmeasured before this file existed.
  const ungoverned = measureToolSurface()
    .filter((t) => CEILINGS[t.name] === undefined)
    .map((t) => `${t.name} (${t.total} chars, in ${t.file})`);
  assert.deepEqual(ungoverned, [],
    "a tool has no ceiling. Measure it, add it to CEILINGS, and say in the commit what an agent "
    + "gains for the context every one of them now pays.");
});

test("a ceiling left slack above the real size is reported", () => {
  // The ratchet only ratchets if the ceilings follow the size DOWN. A ceiling 200 characters above
  // its tool is room to regrow into without any test noticing, which is how a ratchet quietly
  // becomes a cap. Generous enough not to fire on a one-word edit.
  const slack = measureToolSurface()
    .filter((t) => CEILINGS[t.name] !== undefined && CEILINGS[t.name] - t.total > 120)
    .map((t) => `${t.name}: ceiling ${CEILINGS[t.name]}, actual ${t.total}`);
  assert.deepEqual(slack, [],
    "a tool shrank well below its ceiling and the ceiling was not lowered with it. Lower it, so the "
    + "saving is kept rather than left as room to regrow into.");
});

test("a ceiling naming a tool that no longer exists is reported", () => {
  // The list shrinks honestly instead of rotting into names nothing checks.
  const tools = new Set(measureToolSurface().map((t) => t.name));
  const stale = Object.keys(CEILINGS).filter((name) => !tools.has(name));
  assert.deepEqual(stale, [], "a ceiling outlived its tool");
});
