#!/usr/bin/env node
// aify-env decides which agents to offer a START. This service owns the vocabulary that decision
// reads, and the two live in different repos.
//
// WHAT aify-env DOES WITH IT, and why a wrong answer is expensive. Its TUI lists "agents with no
// running worker" and a keystroke restarts one. Offer a status that actually has a worker and the
// agent gets a SECOND -- the shape measured on this fleet on 2026-09-03, where the service
// reconciled live terminals as ghosts, asked for replacements, and the host started them beside the
// ones still running. Offer `starting` and the keystroke kills a spawn that was already coming up.
//
// SO THE RULE TURNS ON MEANING, NOT ON THE WORD, and the meanings live here:
// `service/contracts/vocabulary.json`, `agentStatuses`. aify-env cannot import that file at runtime
// -- separate packages, separate processes, and it must work with no aify-comms checkout present --
// so it holds a copy. This test is what keeps the copy honest.
//
// THE FAILURE IT CATCHES IS A STATUS ADDED HERE AND NOWHERE ELSE. A new `agentStatuses` value is one
// line in a JSON file; nothing about adding it would prompt anyone to open another repository. On
// aify-env's side an unjudged status FAILS CLOSED -- it is simply never offered -- so the fleet stays
// safe and the feature quietly stops covering a state, which is a defect nobody would ever see. This
// test is the thing that sees it.
//
// IT FAILS RATHER THAN SKIPS when the aify-env checkout is absent, matching
// `the-credential-ref-we-write-is-one-aify-env-resolves.test.js`: a cross-repo proof that quietly
// does not run still reports green, which is worse than not having it.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

const REPO = path.resolve(new URL("../../..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const CONTRACT = path.join(REPO, "service", "contracts", "vocabulary.json");
const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const THEIRS = path.join(AIFY_ENV, "lib", "startable-agents.mjs");

/** The statuses this service declares, from the file the service itself reads. */
function ourStatuses() {
  const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8"));
  const values = contract?.agentStatuses?.values;
  assert.ok(Array.isArray(values) && values.length > 0,
    `positive control: ${CONTRACT} declared no agentStatuses.values`);
  return values.map((value) => String(value));
}

test("aify-env judges EVERY agent status this service declares", async () => {
  assert.ok(fs.existsSync(THEIRS),
    `aify-env checkout not found at ${AIFY_ENV} (set AIFY_ENV_REPO). This proof must run, not skip.`);
  const { STARTABLE_STATUSES, NOT_STARTABLE_STATUSES } = await import(`file://${THEIRS.replace(/\\/g, "/")}`);

  const offered = Object.keys(STARTABLE_STATUSES || {});
  const refused = Object.keys(NOT_STARTABLE_STATUSES || {});
  // POSITIVE CONTROL: both maps carry entries. Everything below is satisfied by two empty objects
  // and a contract nobody read, which is exactly the vacuous green this file exists to rule out.
  assert.ok(offered.length > 0, "aify-env offers no status at all — its start menu can never list anything");
  assert.ok(refused.length > 0, "aify-env names no refusal — the map that documents its reasoning is empty");

  const judged = new Set([...offered, ...refused]);
  const unjudged = ourStatuses().filter((status) => !judged.has(status));
  assert.deepEqual(unjudged, [],
    `these agent statuses exist here and aify-env has never judged them: ${unjudged.join(", ")}. `
    + "It fails closed, so the agents in that state are silently unstartable from its menu. "
    + `Add each to STARTABLE_STATUSES or NOT_STARTABLE_STATUSES in ${THEIRS}.`);
});

test("aify-env judges NO status this service has stopped declaring", async () => {
  // The other direction, and the one that rots quietly. A status removed here leaves a rule over
  // there deciding about a word nothing produces any more — and if the removed one was on the
  // OFFERED list, the reader is left believing a state is covered when the fleet can never be in it.
  assert.ok(fs.existsSync(THEIRS), `aify-env checkout not found at ${AIFY_ENV} (set AIFY_ENV_REPO).`);
  const { STARTABLE_STATUSES, NOT_STARTABLE_STATUSES } = await import(`file://${THEIRS.replace(/\\/g, "/")}`);
  const ours = new Set(ourStatuses());
  const stale = [...Object.keys(STARTABLE_STATUSES), ...Object.keys(NOT_STARTABLE_STATUSES)]
    .filter((status) => !ours.has(status));
  assert.deepEqual(stale, [],
    `aify-env judges statuses this service no longer declares: ${stale.join(", ")}`);
});

test("NEGATIVE CONTROL: the comparison can actually report a difference", async () => {
  // Both assertions above pass trivially if `ourStatuses()` returned nothing, or if the set
  // arithmetic were inverted. Feeding a status the contract does not contain must produce a
  // non-empty difference — otherwise the two tests are decoration.
  assert.ok(fs.existsSync(THEIRS), `aify-env checkout not found at ${AIFY_ENV} (set AIFY_ENV_REPO).`);
  const { STARTABLE_STATUSES, NOT_STARTABLE_STATUSES } = await import(`file://${THEIRS.replace(/\\/g, "/")}`);
  const judged = new Set([...Object.keys(STARTABLE_STATUSES), ...Object.keys(NOT_STARTABLE_STATUSES)]);
  const invented = [...ourStatuses(), "a-status-nobody-declares"].filter((status) => !judged.has(status));
  assert.deepEqual(invented, ["a-status-nobody-declares"],
    "the difference is computed wrong — it cannot see a status aify-env has not judged");
});

test("the one that would have cost a spawn: `starting` is on the REFUSED side", async () => {
  // It has no worker, so any rule phrased as "offer anything not live" offers it. The contract says
  // otherwise in as many words, and the cost of getting it wrong is a boot killed mid-flight.
  assert.ok(fs.existsSync(THEIRS), `aify-env checkout not found at ${AIFY_ENV} (set AIFY_ENV_REPO).`);
  const { STARTABLE_STATUSES, NOT_STARTABLE_STATUSES } = await import(`file://${THEIRS.replace(/\\/g, "/")}`);
  const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8"));
  const meanings = contract?.agentStatuses?.meanings || {};
  assert.match(String(meanings.starting || ""), /Do NOT restart/i,
    "positive control: the contract no longer warns against restarting a `starting` agent");
  assert.equal(STARTABLE_STATUSES.starting, undefined, "aify-env offers to restart an agent whose spawn is in flight");
  assert.ok(NOT_STARTABLE_STATUSES.starting, "aify-env does not say why `starting` is refused");
});
