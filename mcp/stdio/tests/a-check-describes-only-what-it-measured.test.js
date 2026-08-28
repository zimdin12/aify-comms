// A doctor check may only assert what its own inputs can tell it.
//
// THE DEFECT, 2026-08-28, and it cost the operator four restarts of the wrong component.
//
// `bridgeInstallVerdict` is pure. Its inputs are two shas and two commit counts, all read from FILES:
// the installed version marker and the checkout. Nothing in them describes a process. Its stale
// branch nevertheless said:
//
//     "The RUNNING bridge is older than the checkout."
//
// That is the sentence from `serviceBuildVerdict` one check over, where it is TRUE, because that one
// is handed the sha a running container reports on /version. Copied here it became a claim from an
// instrument that cannot see the thing it names.
//
// WHY IT MATTERED RATHER THAN BEING A WORDING NIT. The operator's symptom was a stale PROCESS: the
// environment bridge was running 579dd546 while the files on disk were 45045505 and already carried
// the fix they were chasing. `aify-comms doctor` showed BOTH checks red and BOTH mentioning the
// running bridge, and the advice that reads first says re-run install.sh -- the wrong lever, which
// would have changed nothing. `bridge-current`'s own escape clause ("re-running install.sh will not
// help if bridge-installed is already green") could not fire, because bridge-installed was
// legitimately red for seven other commits.
//
// THE GATE IS NOT "never say RUNNING", and the first version of it was. That version failed on the
// REPLACEMENT text, correctly: the fixed detail says "whether a bridge is RUNNING is a separate
// question that bridge-current answers", which mentions a process in order to DISCLAIM it. Banning
// the word would have forced word-golf around a rule that was aimed at the wrong thing.
//
// The property is a claim SHAPE: a process noun and a staleness verdict in the SAME SENTENCE. "The
// RUNNING bridge is older than the checkout" has both and is the defect. A deferral has the noun and
// no verdict. A statement about files has the verdict and no process noun.
//
// AND ITS CONTROL IN THE SAME RUN. A scanner that matches nothing passes whatever it is pointed at,
// so the same scanner is pointed at `serviceBuildVerdict`, whose stale branch says exactly this about
// a process and is ENTITLED to -- it is handed the sha a running container reports. Absent from one,
// present in the other, one instrument, one run.
import assert from "node:assert/strict";
import { test } from "node:test";

import { bridgeInstallVerdict, serviceBuildVerdict } from "../doctor-predicates.js";

/** Nouns naming a live process rather than bytes on disk. */
const PROCESS_NOUNS = ["running", "in memory", "loaded at boot", "serving"];

/** Verdicts about being out of date. A process noun with one of these is a claim; without, it is not. */
const STALENESS_VERDICTS = ["older", "newer", "stale", "behind", "out of date"];

/** Sentences that assert a staleness verdict ABOUT a process. */
function claimsAboutAProcess(verdict) {
  const text = `${verdict.detail ?? ""} ${verdict.fix ?? ""}`;
  // Split on sentence ends only. A claim is confined to one sentence; joining the whole string would
  // convict a paragraph that mentions files in one sentence and a process in the next.
  return text
    .split(/(?<=[.!?])\s+/)
    .filter((sentence) => {
      const lower = sentence.toLowerCase();
      return PROCESS_NOUNS.some((noun) => lower.includes(noun))
        && STALENESS_VERDICTS.some((word) => lower.includes(word));
    });
}

/** Every branch of the install verdict, reached by input rather than listed by hand. */
const INSTALL_CASES = [
  { name: "no marker", input: {} },
  { name: "no checkout", input: { installedSha: "aaaaaaabbb" } },
  { name: "current", input: { installedSha: "aaaaaaabbb", headSha: "aaaaaaabbb", headShort: "aaaaaaa" } },
  {
    name: "stale, bridge commits",
    input: { installedSha: "4504550aaa", headSha: "6683c9ebbb", headShort: "6683c9e", bridgeCommits: 7, totalCommits: 67 },
  },
  {
    name: "behind by commits that miss the bridge",
    input: { installedSha: "4504550aaa", headSha: "6683c9ebbb", headShort: "6683c9e", bridgeCommits: 0, totalCommits: 12 },
  },
];

test("no branch of the install verdict claims anything about a running process", () => {
  for (const { name, input } of INSTALL_CASES) {
    const verdict = bridgeInstallVerdict(input);
    const claims = claimsAboutAProcess(verdict);
    assert.deepEqual(
      claims, [],
      `bridgeInstallVerdict("${name}") passes a staleness verdict on a PROCESS: ${JSON.stringify(claims)}. `
        + `Its inputs are `
        + "two shas and two commit counts, all read from files -- it cannot know what any process is "
        + "executing. bridge-current owns that question and is handed the sha a live bridge reports.",
    );
  }
});

test("CONTROL: the service verdict DOES claim it, because it measured it", () => {
  // Without this the test above passes on a scanner that matches nothing, which is the wrong zero
  // this repo has produced more than once.
  const stale = serviceBuildVerdict({
    healthy: true, builtSha: "4504550aaa", headSha: "6683c9ebbb", headShort: "6683c9e", runtimeCommits: 37,
  });
  assert.ok(
    claimsAboutAProcess(stale).length > 0,
    "the scanner found no process claim in serviceBuildVerdict's stale branch, where one belongs -- so "
      + "it would not have found the one in bridgeInstallVerdict either",
  );
});

test("the stale install verdict says where the code IS, and points at the check that owns the rest", () => {
  const verdict = bridgeInstallVerdict({
    installedSha: "4504550aaa", headSha: "6683c9ebbb", headShort: "6683c9e", bridgeCommits: 7, totalCommits: 67,
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /ON DISK/, "the check must say which of the two things it measured");
  assert.match(
    verdict.detail, /bridge-current/,
    "an operator reading only this line has to be told which check answers the process question, or "
      + "they will act on this one",
  );
});

test("the install fix still asks for the relaunch, attributed to the check that knows about it", () => {
  // The relaunch is REAL and must not be lost: install.sh puts code on disk and a running bridge
  // keeps what it loaded. Removing the sentence would trade a misattributed instruction for a
  // missing one, which is not an improvement.
  const verdict = bridgeInstallVerdict({
    installedSha: "4504550aaa", headSha: "6683c9ebbb", headShort: "6683c9e", bridgeCommits: 7, totalCommits: 67,
  });
  assert.match(verdict.fix, /install\.sh/, "the action this check owns is gone");
  assert.match(verdict.fix, /relaunch/, "the relaunch is still needed after an install and must be said");
  assert.match(verdict.fix, /bridge-current/, "the relaunch is attributed to the check that names which");
});

test("a clean install verdict stays quiet", () => {
  // The other half of this check's history: it used to fire on docs-only commits, which trains an
  // operator to skim past it. Both failure modes are "the line stops being read".
  const clean = bridgeInstallVerdict({
    installedSha: "4504550aaa", headSha: "6683c9ebbb", headShort: "6683c9e", bridgeCommits: 0, totalCommits: 12,
  });
  assert.equal(clean.ok, true);
  assert.equal(clean.fix, "");
});
