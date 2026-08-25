#!/usr/bin/env node
// A test-only commit does not demand a reinstall, because its remedy reaps the fleet.
//
// `bridge-installed` counts commits since the installed marker that touched `mcp/stdio`. It counted
// commits that touched ONLY `mcp/stdio/tests`. install.sh copies the whole directory into
// ~/.aify-comms -- 349 test files when this was written -- and nothing ever executes them from there;
// every suite runs from the checkout. So such a commit cannot change one byte the bridge runs.
//
// WHAT MAKES THIS WORTH FIXING RATHER THAN NOTING is the remedy attached to the red. The check's fix
// line is "re-run install.sh AND relaunch the wrappers", and relaunching the environment bridge reaps
// its managed workers. A test-only commit could therefore ask an operator to kill a working fleet for
// a change with no runtime effect -- from a check whose own documentation says it must not cry wolf.
//
// This is the FOURTH instance of the class already documented beside SERVICE_RUNTIME_EXCLUDE_PATHS,
// which excludes `service/tests` for exactly the same reason and was itself "found by the fix flagging
// its OWN commit". The rule was never carried across to the bridge.
//
// Measured on the real repo: since the installed sha, 4 commits touched mcp/stdio and 3 carried
// runtime files; e19ae974 touched only mcp/stdio/tests. With the exclusion the range counts 3, and
// that commit ALONE counts 0 instead of 1.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { BRIDGE_RUNTIME_EXCLUDE_PATHS } from "../doctor-predicates.js";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

const git = (args) =>
  execFileSync("git", args, { cwd: REPO, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();

/** The pathspec doctor.js builds: the bridge directory, minus the excluded ones. */
function bridgePathspec() {
  return ["mcp/stdio", ...BRIDGE_RUNTIME_EXCLUDE_PATHS.map((p) => `:(exclude)${p}`)];
}

// ── the list itself ────────────────────────────────────────────────────────────────────────────
{
  assert.ok(Array.isArray(BRIDGE_RUNTIME_EXCLUDE_PATHS), "the exclude list is gone");
  assert.ok(
    BRIDGE_RUNTIME_EXCLUDE_PATHS.includes("mcp/stdio/tests"),
    "the bridge test directory is counted as runtime again, so a test-only commit demands a wrapper "
    + "relaunch that reaps managed workers",
  );
  // Opt-OUT, deliberately: a new directory under mcp/stdio should default to runtime and demand a
  // reinstall. An exclude list that grew to cover most of the tree would be a false green.
  assert.ok(BRIDGE_RUNTIME_EXCLUDE_PATHS.length < 5, "the exclusion has widened past a reasoned few");
}

// ── against the real repository ────────────────────────────────────────────────────────────────
{
  // A commit that touched ONLY bridge tests. Named rather than searched: a search that found nothing
  // would make this file pass while proving nothing, which is the failure mode it exists to catch.
  const testOnly = "e19ae974";
  let known = true;
  try { git(["cat-file", "-e", `${testOnly}^{commit}`]); } catch { known = false; }

  if (known) {
    const withTests = Number(git(["rev-list", "--count", `${testOnly}^..${testOnly}`, "--", "mcp/stdio"]));
    const without = Number(git(["rev-list", "--count", `${testOnly}^..${testOnly}`, "--", ...bridgePathspec()]));
    assert.equal(withTests, 1, "the named commit no longer touches mcp/stdio; pick another");
    assert.equal(
      without, 0,
      "a commit touching only mcp/stdio/tests still counts as a bridge change, so bridge-installed "
      + "would still demand a fleet-reaping relaunch for it",
    );
  } else {
    // A shallow clone or a rewritten history. Say so rather than passing silently.
    console.error("  (skipped the real-repo check: commit e19ae974 is not in this history)");
  }
}

// ── and the exclusion does not swallow real bridge changes ─────────────────────────────────────
{
  // The other direction, and the one that matters more: an exclusion that hid runtime edits would
  // turn this check permanently green — a false green, the worse failure.
  const runtimeCommit = "48bf9259";   // the AIFY_COMMS_AGENT_ROLE alias fix
  let known = true;
  try { git(["cat-file", "-e", `${runtimeCommit}^{commit}`]); } catch { known = false; }
  if (known) {
    const counted = Number(
      git(["rev-list", "--count", `${runtimeCommit}^..${runtimeCommit}`, "--", ...bridgePathspec()]),
    );
    assert.equal(
      counted, 1,
      "a commit that changed bridge RUNTIME code is no longer counted; the exclusion is too wide and "
      + "bridge-installed would report clean while the bridge on disk is stale",
    );
  }
}

console.log("doctor-bridge-install-staleness.test.js: all assertions passed");
