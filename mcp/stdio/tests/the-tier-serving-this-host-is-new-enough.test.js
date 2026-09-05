#!/usr/bin/env node
// The aify-env SERVING this host must be new enough for the aify-comms installed on it.
//
// EXTERNAL REVIEW, Round 8, preamble: "aify-env/aify-wrapper both still say 0.6.0 with no tags --
// nothing gates cross-tier version agreement." Three repos ship one product and nothing compared
// them, in a checkout or on a live host.
//
// `bridgeVersion` WAS ALREADY ON THE WIRE AND READ BY NOBODY -- one hit in the whole bridge, and that
// a comment. A field with no reader is this repo's own recurring defect; M8 was the other instance
// found in the same round.
//
// WHAT GOES WRONG WITHOUT IT, measured this round rather than imagined. H4's fix has two ends: the
// service prefers a host tier over a retired bridge, and it can only do that because aify-env sends
// `metadata.bridgeKind`. An aify-comms carrying that fix against an aify-env too old to send one
// takes the legacy path silently -- both sides healthy, the feature absent, nothing saying so. That
// is the eight-day shape this project has already paid for.
//
// A MINIMUM, NOT EQUALITY, because the tiers are separate products on separate cadences -- which is
// the whole point of the three-repo split, and aify-dashboard will consume the same tiers.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  MINIMUM_AIFY_ENV_VERSION,
  compareVersions,
  parseVersion,
  tierVersionVerdict,
} from "../tier-version-check.mjs";

/** A live environment row as `/environments` serves one. */
const row = (id, { kind = "aify-env", version = MINIMUM_AIFY_ENV_VERSION } = {}) => ({
  id,
  bridgeVersion: version,
  metadata: kind === null ? {} : { bridgeKind: kind },
});
const allLive = () => true;

test("versions compare by number, not by string", () => {
  // `"0.10.0" < "0.9.0"` lexically, and a host on 0.10 would be told to upgrade to 0.9.
  assert.deepEqual(parseVersion("0.6.2"), [0, 6, 2]);
  assert.equal(parseVersion("not a version"), null);
  assert.ok(compareVersions("0.10.0", "0.9.0") > 0, "0.10 must be NEWER than 0.9");
  assert.ok(compareVersions("0.6.1", "0.6.2") < 0);
  assert.equal(compareVersions("0.6.2", "0.6.2"), 0);
  assert.equal(compareVersions("nonsense", "0.6.2"), null, "an unreadable version is not an order");
});

test("a tier at the minimum passes, and so does a NEWER one", () => {
  // The independence the three-repo split exists for: aify-env moving ahead is normal and must not
  // be reported as a problem.
  for (const version of [MINIMUM_AIFY_ENV_VERSION, "0.7.0", "1.0.0"]) {
    const verdict = tierVersionVerdict({ environments: [row("e1", { version })], isLive: allLive });
    assert.equal(verdict.ok, true, `aify-env ${version} was reported as too old`);
  }
});

test("A TIER BEHIND THE MINIMUM IS NAMED, with its version and the remedy", () => {
  const verdict = tierVersionVerdict({
    environments: [row("windows:host:default", { version: "0.6.0" })],
    isLive: allLive,
  });
  assert.equal(verdict.ok, false, "an aify-env two versions behind was reported as fine");
  assert.equal(verdict.code, "tier-too-old");
  assert.match(verdict.detail, /windows:host:default/, "the host must be NAMED, or nobody can act");
  assert.match(verdict.detail, /0\.6\.0/, "and the version it is actually running");
  assert.match(verdict.fix, /restart it/i, "the remedy has to say what to do");
});

test("A ROW THAT DECLARES NO KIND IS UNVERIFIED, NOT SKIPPED", () => {
  // THE BUG THIS CHECK SHIPPED WITH FOR TEN MINUTES, caught by running it against the operator's own
  // host: it reported GREEN while the aify-env serving that host was two versions behind.
  //
  // The reasoning that produced it was that `bridgeKind` marks the host tier, so a row without one is
  // a legacy aify-comms bridge and somebody else's problem. It is ALSO what an aify-env too old to
  // declare a kind looks like -- which is exactly the case this check exists for. Scoping to rows
  // that announce themselves scoped OUT every row that is behind.
  const verdict = tierVersionVerdict({
    environments: [{ id: "windows:old:default", bridgeVersion: "", metadata: {} }],
    isLive: allLive,
  });
  assert.equal(verdict.ok, false,
    "a row declaring neither a kind nor a version was treated as agreement. That is the shape of an "
    + "aify-env too old to declare either, which is what this check is for.");
  assert.equal(verdict.code, "unknown-all");
});

test("a row declaring some OTHER tier is left to the checks that own it", () => {
  // A legacy aify-comms bridge is a different problem with a different remedy -- H4's refusal and
  // `bridge-current` both name it. Reporting it here too would give an operator two rows and one
  // action, and they would learn to skim both.
  const verdict = tierVersionVerdict({
    environments: [row("e1", { kind: "something-else", version: "0.0.1" })],
    isLive: allLive,
  });
  assert.equal(verdict.ok, true, "a row belonging to another tier was judged against aify-env's minimum");
});

test("A DEAD environment is not judged, because its version is a fact about nothing", () => {
  // Reporting a registered-but-dead host would make this row red for a machine nobody is using, and
  // a check that cries wolf is a check that gets switched off.
  const verdict = tierVersionVerdict({
    environments: [row("e1", { version: "0.0.1" })],
    isLive: () => false,
  });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "none-live",
    "'nothing to compare' and 'compared and agreed' are different answers");
});

test("the minimum is a real version and is DECLARED WITH ITS REASON", () => {
  // A minimum nobody can justify gets raised on a whim and then ignored. The reason lives beside the
  // constant so a bump has to argue for itself.
  assert.ok(parseVersion(MINIMUM_AIFY_ENV_VERSION), "the minimum is not a parseable version");
  const text = readFileSync(new URL("../tier-version-check.mjs", import.meta.url), "utf8");
  assert.match(text, /bridgeKind/,
    "the declared minimum no longer says WHICH capability it is about. A version with no reason "
    + "attached is one nobody can decide to raise or lower.");
});

test("A KINDLESS ROW REPORTING A HIGH VERSION IS STILL UNVERIFIED", () => {
  // R9-M6, external review 2026-09-06, and it is the same false green wearing a different hat.
  //
  // The test above pins a kindless row with NO version. The branch handled that, and handled a
  // version that was OLDER or UNPARSEABLE -- and let one that parses NEWER fall straight through
  // recorded as nothing, which the verdict counts as agreement.
  //
  // The number is not hypothetical. Before the VERSION file existed the legacy aify-comms bridge
  // hardcoded `4.0.0` in eight places, and this host still has kindless rows reporting exactly that.
  // `4.0.0 >= 0.6.2`, so every one of them was being counted as a compliant aify-env.
  const verdict = tierVersionVerdict({
    environments: [{ id: "windows:legacy:default", bridgeVersion: "4.0.0", metadata: {} }],
    isLive: allLive,
  });
  assert.equal(
    verdict.ok, false,
    "a row that declares no tier kind was counted as a compliant aify-env because the version it "
    + "reported happened to sort high. Nothing about that row says it is an aify-env at all.",
  );
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /4\.0\.0/, "the operator has to see WHICH version was disbelieved");
});

test("a kindless row does not drag a properly declared aify-env down with it", () => {
  // The control. If an undeclared row poisoned the whole verdict, an operator with one legacy row
  // could never see a green tier-version and would stop reading it.
  const kindless = { id: "windows:legacy:default", bridgeVersion: "4.0.0", metadata: {} };
  const declared = row("windows:good:default", { kind: "aify-env", version: "9.9.9" });
  const both = tierVersionVerdict({ environments: [kindless, declared], isLive: allLive });
  const alone = tierVersionVerdict({ environments: [declared], isLive: allLive });

  assert.equal(alone.ok, true, "a declared, new-enough aify-env must read green on its own");
  assert.equal(alone.code, "ok");
  // With both present the honest answer is still "something here was not verified" -- but it must
  // name the kindless row, not the healthy one.
  assert.equal(both.ok, false);
  assert.match(both.detail, /legacy/, "the unverified row is the one that must be named");
  assert.doesNotMatch(both.detail, /9\.9\.9/, "the healthy aify-env must not be reported as a problem");
});
