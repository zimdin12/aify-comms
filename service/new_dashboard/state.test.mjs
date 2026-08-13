// The seeded shape of the dashboard's state object, asserted by READING IT rather than by grepping for it.
//
// This replaces `test_state_seeds_settings_snapshot` in
// `service/tests/test_new_dashboard_session_mode_switch.py`, which asserted that the literal text
// `settings: {}` appeared somewhere in app.js. That is a location pin: it proves a line was written, not
// that the object has the property, and it fails the moment the declaration moves — which is exactly what
// it did when `state` was given its own module in v0.5.4, even though nothing about the behaviour changed.
//
// Now that `state.mjs` holds nothing but data and touches no browser global, it can simply be imported and
// checked. That is worth more than the regex ever was: a typo like `settings: null` would have satisfied
// neither test, but `settings: {},` inside a COMMENT would have satisfied the old one.
//
// WHY THE SEEDING MATTERS (carried over from the Plan 6 C3/C4 note on the retired test): `state.settings`
// must be an object before the first refresh completes, because `renderModeSwitchChip` reads
// `manual_session_mode` off it during the very first render. Seeded as `null` or left undefined, the first
// paint throws instead of showing the chip's default.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";

test("state.settings is seeded as an object, so the first render can read it", () => {
  assert.deepEqual(state.settings, {},
    "renderModeSwitchChip reads manual_session_mode off state.settings during the first paint, before any "
    + "refresh has populated it");
});

test("the collection fields are seeded as arrays, not null", () => {
  // Same class of first-paint failure: every list renderer maps over these before the first refresh.
  for (const key of ["agents", "contracts"]) {
    assert.ok(Array.isArray(state[key]), `state.${key} must be an array on a cold load, before any fetch`);
    assert.equal(state[key].length, 0, `state.${key} must start empty`);
  }
});

test("loaded starts false, which is what distinguishes a cold load from an empty fleet", () => {
  // The chat rail shows "Loading…" rather than "No agents." while this is false. Seeding it `true` would
  // report an empty fleet to the operator on every page open until the first refresh landed.
  assert.equal(state.loaded, false);
});
