// The aify-wrapper this repo consumes is the one it means to consume, and nothing silently replaced it.
//
// THE MISS THIS EXISTS FOR, 2026-08-29. aify-comms pinned `94b5716`. aify-wrapper's HEAD was
// `bb56df5`, three commits later, and the top one was
//
//     bb56df5 fix(claude launcher): stop inheriting another session's child-session marker
//
// the fix for a defect the operator had been reporting all day: every resident session started from a
// shell inside Claude Code silently lost its transcript. That commit's own message ended "NOT DEPLOYED
// -- this needs install.sh re-run and every wrapper relaunched". install.sh WAS re-run on that host.
// It rendered the old template, because the pin still pointed before the fix.
//
// NOTHING WAS LOOKING. `bridge-installed` compares the installed bridge to the checkout.
// `bridge-current` compares a running bridge to the checkout. `wrapper-current` is aify-wrapper's own
// question about launchers on disk. None reads the dependency pin.
//
// TWO QUESTIONS, AND THE FIRST VERSION COLLAPSED THEM.
//   CONSUMED  -- deterministic, offline, no sibling checkout: full sha, package and lock agree,
//                node_modules holds that pin. This is the gate.
//   UPSTREAM  -- environment-dependent and ADVISORY: has upstream landed something we render?
//                A pin deliberately selects a version, so "behind HEAD" is not the same as stale, and
//                a hard failure whenever aify-wrapper moves would defeat independent versioning.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  CONSUMED_SURFACE, consumedPinVerdict, pinnedWrapperSha, upstreamAdvisory,
} from "../wrapper-pin-freshness.mjs";

const BRIDGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);

// ---- the consumed gate: deterministic, and the one that must always run --------------------------

test("a consistent pin passes", () => {
  assert.equal(consumedPinVerdict({ packagePin: SHA_A, lockPin: SHA_A, installedPin: SHA_A }).ok, true);
});

test("a lock or a node_modules that disagrees is the failure", () => {
  // The bytes that get rendered are the installed ones. package.json states intent.
  assert.equal(consumedPinVerdict({ packagePin: SHA_A, lockPin: SHA_B, installedPin: SHA_A }).code, "disagree");
  assert.equal(consumedPinVerdict({ packagePin: SHA_A, lockPin: SHA_A, installedPin: SHA_B }).code, "disagree");
});

test("an unpinned dependency is refused", () => {
  assert.equal(consumedPinVerdict({ packagePin: "" }).code, "unpinned");
});

test("NEITHER RECORD PRESENT IS NOT AGREEMENT", () => {
  // A caller that read no lock and no installed tree compared the pin against nothing. Reporting
  // "consistent" there is the false green this file exists to prevent.
  assert.equal(consumedPinVerdict({ packagePin: SHA_A }).code, "unknown");
});

test("no arguments does not pass", () => {
  assert.equal(consumedPinVerdict().ok, false);
});

// ---- the upstream advisory, and the false green it produced in its first hour --------------------

test("EVERY NON-ANSWER IS UNKNOWN, never ok", () => {
  // THE BUG THIS TEST EXISTS FOR, in code I wrote an hour earlier. The caller mapped every git failure
  // to "", so a pin that is NOT AN ANCESTOR -- a force-push, a divergent checkout, a sha from a branch
  // -- made `git log pin..HEAD` fail, produced an empty commit list, and the verdict read
  // `ok: true, "0 commits ahead, none touching the templates"`. A confident answer assembled out of no
  // evidence, inside a check written to catch exactly that. Caught in review, not by me.
  for (const status of ["no-repo", "pin-not-ancestor", "query-failed", "something-new"]) {
    const verdict = upstreamAdvisory({ status, pin: SHA_A, head: SHA_B, consumedSurfaceCommits: [] });
    assert.equal(verdict.ok, false, `${status} reported ok`);
    assert.equal(verdict.code, "unknown", `${status} was not reported as unknown`);
  }
});

test("pin-not-ancestor says WHY, because the remedy differs", () => {
  // "Nothing was compared" sends somebody to clone a repo. "Your pin is not on this history" sends
  // them to look at which branch they pinned.
  const verdict = upstreamAdvisory({ status: "pin-not-ancestor", pin: SHA_A, head: SHA_B });
  assert.match(verdict.detail, /not an ancestor/);
});

test("behind by a commit that touches the consumed surface is reported", () => {
  const verdict = upstreamAdvisory({
    status: "ok", pin: SHA_A, head: SHA_B,
    consumedSurfaceCommits: ["bb56df5 fix(claude launcher): stop inheriting another session's marker"],
  });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "behind-consumed-surface");
  assert.match(verdict.detail, /child-session marker|marker/);
  // A bump can bring a template PARAMETER as well as a fix, which is how @@SERVICE_NAME@@ arrived and
  // hung the wrapper behaviour suite. The remedy has to say so.
  assert.match(verdict.fix, /@@NAME@@|unsubstituted/);
});

test("behind by commits that cannot change what we render is quiet", () => {
  // The same rule `bridgeInstallVerdict` follows: an alarm that fires on commits it has no opinion
  // about is one an operator learns to skim.
  const verdict = upstreamAdvisory({ status: "ok", pin: SHA_A, head: SHA_B, totalCommits: 3 });
  assert.equal(verdict.ok, true);
  assert.match(verdict.detail, /none touching the consumed surface/);
});

test("the consumed surface is wider than the templates", () => {
  // `wrappers/` alone caught today's defect BY LUCK. This repo also executes the package's registry
  // CLI and reads its installed-endpoint utilities, so a template-only claim is a template claim.
  assert.ok(CONSUMED_SURFACE.includes("wrappers/"));
  assert.ok(CONSUMED_SURFACE.includes("lib/"), "the package's lib/ is executed by this repo");
});

// ---- reading the pin -----------------------------------------------------------------------------

test("only a FULL sha is read as a pin", () => {
  assert.equal(pinnedWrapperSha(`"aify-wrapper": "github:x/y#${SHA_A}"`), SHA_A);
  assert.equal(pinnedWrapperSha('"aify-wrapper": "github:x/y#bb56df5"'), "",
    "a short sha was accepted; npm records the full one and the two would never compare equal");
  assert.equal(pinnedWrapperSha('"aify-wrapper": "^1.2.3"'), "");
  assert.equal(pinnedWrapperSha(""), "");
});

// ---- against this repo, deterministically ---------------------------------------------------------

test("THE GATE: this repo consumes exactly the pin it declares", () => {
  // No sibling checkout required. A clean clone, CI, or a package consumer runs this.
  const packagePin = pinnedWrapperSha(readFileSync(path.join(BRIDGE, "package.json"), "utf8"));
  const lockPin = pinnedWrapperSha(readFileSync(path.join(BRIDGE, "package-lock.json"), "utf8"));
  let installedPin = "";
  const installed = path.join(BRIDGE, "node_modules", "aify-wrapper");
  if (existsSync(path.join(installed, ".git"))) {
    installedPin = execFileSync("git", ["rev-parse", "HEAD"], { cwd: installed, encoding: "utf8" }).trim();
  } else if (existsSync(installed)) {
    // npm strips `.git` from a git dependency, so the installed TREE cannot name its own sha -- but
    // `node_modules/.package-lock.json` can, and it is written by the install that actually
    // happened. This read `installedPin = packagePin` until 2026-09-04 (external review, Round 8
    // M14), which made the comparison pin-to-LOCK and never pin-to-DISK: the gate passed by
    // construction on exactly the failure it exists to catch.
    //
    // THAT FAILURE IS DOCUMENTED IN THIS REPO AND WAS MEASURED: bumping the sha in both package
    // files and running `npm install` reported success and left `node_modules/aify-wrapper` holding
    // the PREVIOUS code, because npm trusts a tree that matches the lock it was just handed. This
    // gate was green throughout.
    const installedLock = path.join(BRIDGE, "node_modules", ".package-lock.json");
    if (existsSync(installedLock)) {
      const tree = JSON.parse(readFileSync(installedLock, "utf8"));
      const entry = Object.entries(tree?.packages || {})
        .find(([name]) => name.endsWith("node_modules/aify-wrapper"));
      const resolved = String(entry?.[1]?.resolved || "");
      const at = resolved.lastIndexOf("#");
      // Only a real 40-char sha counts. A `resolved` naming a branch or a tag says nothing about
      // which commit is on disk, and reading it as one would rebuild the same false pass.
      const sha = at >= 0 ? resolved.slice(at + 1) : "";
      if (/^[0-9a-f]{40}$/.test(sha)) installedPin = sha;
    }
    // STILL NO WITNESS: leave it empty rather than substituting the pin. `consumedPinVerdict` is
    // then comparing two values instead of three, which is honest; fabricating a third that AGREES
    // BY CONSTRUCTION is what made this gate vacuous.
  }
  const verdict = consumedPinVerdict({ packagePin, lockPin, installedPin });
  assert.ok(verdict.ok, `${verdict.detail}\n${verdict.fix}`);
});

// ---- the upstream advisory, only where it can be answered ----------------------------------------

test("THE ADVISORY: what upstream has landed since the pin", (t) => {
  const repo = [
    process.env.AIFY_WRAPPER_REPO,
    path.join(homedir(), "projects", "aify-wrapper"),
  ].find((dir) => dir && existsSync(path.join(dir, ".git")));

  if (!repo) {
    // A SKIP, DELIBERATELY, and it is not the same call as the cross-repo integration proofs. Those
    // drive a real aify-env and fail when it is absent, because an unrun integration must not read as
    // green. This one compares a pin against an upstream that a clean clone has no business having,
    // and the DETERMINISTIC gate above already ran. Skipping what cannot be asked is honest; skipping
    // what could have been asked is not.
    t.skip("no aify-wrapper checkout: the consumed-pin gate above still ran");
    return;
  }

  const git = (args) => {
    try {
      return {
        status: "ok",
        out: execFileSync("git", args, { cwd: repo, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(),
      };
    } catch {
      return { status: "query-failed", out: "" };
    }
  };

  const pin = pinnedWrapperSha(readFileSync(path.join(BRIDGE, "package.json"), "utf8"));
  const head = git(["rev-parse", "HEAD"]);
  // ANCESTRY FIRST. Without it a divergent pin makes every later query fail, and an empty result reads
  // as "nothing landed" rather than "this question does not apply".
  const ancestor = git(["merge-base", "--is-ancestor", pin, "HEAD"]);
  let status = "ok";
  if (head.status !== "ok") status = "query-failed";
  else if (ancestor.status !== "ok") status = "pin-not-ancestor";

  const log = status === "ok" ? git(["log", "--oneline", `${pin}..HEAD`, "--", ...CONSUMED_SURFACE]) : { status, out: "" };
  const count = status === "ok" ? git(["rev-list", "--count", `${pin}..HEAD`]) : { status, out: "0" };
  if (status === "ok" && (log.status !== "ok" || count.status !== "ok")) status = "query-failed";

  const verdict = upstreamAdvisory({
    status,
    pin,
    head: head.out,
    consumedSurfaceCommits: log.out.split("\n").map((line) => line.trim()).filter(Boolean),
    totalCommits: Number(count.out || 0),
  });
  assert.ok(verdict.ok, `${verdict.detail}\n${verdict.fix}`);
});
