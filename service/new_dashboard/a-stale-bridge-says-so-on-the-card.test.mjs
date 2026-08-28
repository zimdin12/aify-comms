// An environment whose bridge is running a different build than the service says so on its card.
//
// THE INCIDENT THIS COMES FROM, 2026-08-28. The operator restarted aify-env twice trying to make an
// empty AGENT column fill in. It never could: that column is filled from a label the aify-comms
// BRIDGE sends at spawn time, and the running bridge predated that code by two commits -- build
// 579dd546 against a service built from 45045505, 231 commits apart. Every link downstream was
// correct: the database held real agent ids, the service emitted them, the installed bridge files
// contained the fix. Only the running PROCESS was old, because a bridge keeps the code it loaded at
// boot.
//
// `aify-comms doctor` had been saying this all along under `bridge-current`. That is a command you
// have to know to run. The environment card is where somebody looks when an environment misbehaves,
// and it said nothing -- so two restarts went into the one component that could not be the cause.
//
// A MISMATCH IS NOT AN ERROR. The service is a container build; the bridge is host code installed
// separately. They differ routinely between a rebuild and a wrapper relaunch. The badge states the
// fact and the remedy, and deliberately does not touch the status chip, which answers "is this
// environment reachable" and would be wrong to answer this with.

import assert from "node:assert/strict";
import { test } from "node:test";

import { staleBridgeBadge } from "./environments-panels.mjs";

const SERVICE = "45045505";
const OLD_BRIDGE = "579dd546";

const envWith = (bridgeBuild) => ({ id: "windows:host:default", metadata: { bridgeBuild } });

test("a bridge on a different build is named, with both builds and the remedy", () => {
  const html = staleBridgeBadge(envWith(OLD_BRIDGE), SERVICE);
  assert.match(html, new RegExp(OLD_BRIDGE), "the bridge's build is not shown");
  assert.match(html, new RegExp(SERVICE), "the service's build is not shown");
  assert.match(html, /Relaunch/i, "the badge does not say what to do about it");
  assert.match(
    html, /reinstalling alone does not/i,
    "the badge must rule out the wrong remedy: the files were already installed in the real case",
  );
});

test("a bridge on the SAME build says nothing", () => {
  // The control. A badge that always renders is furniture, and every assertion above would pass on
  // one that ignored its arguments entirely.
  assert.equal(staleBridgeBadge(envWith(SERVICE), SERVICE), "");
});

test("a missing build on either side is not a mismatch", () => {
  // ABSENCE IS NOT EVIDENCE. A bridge too old to report its build, and a `/version` that has not
  // answered yet, are both nothing -- and a badge that appeared on every dashboard load until the
  // first poll completed is one nobody would read twice.
  assert.equal(staleBridgeBadge({ id: "e", metadata: {} }, SERVICE), "");
  assert.equal(staleBridgeBadge({ id: "e" }, SERVICE), "");
  assert.equal(staleBridgeBadge(envWith(OLD_BRIDGE), ""), "");
  assert.equal(staleBridgeBadge(undefined, SERVICE), "");
});

test("the builds are compared as text, not truncated or normalised", () => {
  // A short sha compared against a long one would report every environment as stale for ever. Both
  // sides are already short in the payload; this pins that nothing starts trimming one of them.
  assert.equal(staleBridgeBadge(envWith("45045505"), "45045505"), "");
  assert.notEqual(staleBridgeBadge(envWith("450455054285"), "45045505"), "",
    "a long sha and its short form are DIFFERENT strings; if the payload ever carries the long one "
      + "this badge must be taught to compare prefixes rather than silently matching");
});

test("the title is escaped, because it is built from payload values", () => {
  const html = staleBridgeBadge(envWith('"><script>x</script>'), SERVICE);
  assert.doesNotMatch(html, /<script>/, "a hostile bridgeBuild reached the attribute unescaped");
});
