// The wrapper templates in node_modules are the BYTES of the commit package.json pins.
//
// WHY THIS EXISTS, and it is the one half of the pin question nothing asserted.
// `the-wrapper-pin-is-not-behind-a-template-change.test.js` compares three RECORDS -- the sha in
// package.json, the sha the lockfile resolved, and the sha `node_modules/.package-lock.json` says
// was installed. All three can agree while the files on disk are the previous release's, because
// every one of them is npm's account of an install rather than the install's result.
//
// THAT IS NOT HYPOTHETICAL. CLAUDE.md records it as MEASURED: the sha was raised in package.json AND
// package-lock.json, `npm install` reported success, and `node_modules/aify-wrapper` still held the
// previous code -- npm trusts a tree that matches the lock it was just handed. The remedy written
// there is "remove node_modules/aify-wrapper and reinstall, then GREP the installed file for
// whatever the bump was for". This is that grep, run by the suite instead of remembered.
//
// WHAT IT COMPARES. Every file of the pinned commit that npm actually publishes, byte for byte,
// against the copy in node_modules. Files the package does not ship (its own tests, CI config) are
// absent from node_modules by design and are not a finding; a file that is PRESENT and DIFFERENT is.
//
// IT SKIPS BY NAME RATHER THAN PASSING. Answering needs the upstream objects, which a clean clone
// has no business holding, so with no checkout this reports a skip that names what went unasked --
// the property CLAUDE.md requires of every cross-repo proof, because an unrun check must not read
// as green.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pinnedWrapperSha } from "../wrapper-pin-freshness.mjs";

const BRIDGE = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const INSTALLED = path.join(BRIDGE, "node_modules", "aify-wrapper");

//: The artifact the pin exists for. If npm shipped none of these, a nonzero comparison count would
//: be made up of incidental files and would say nothing about what `install.sh` renders.
const TEMPLATES = [
  "wrappers/claude-aify.sh.in",
  "wrappers/codex-aify.sh.in",
  "wrappers/hermes-aify.sh.in",
  "wrappers/pi-aify.sh.in",
];

function upstreamCheckout() {
  return [
    process.env.AIFY_WRAPPER_REPO,
    path.join(homedir(), "projects", "aify-wrapper"),
  ].find((dir) => dir && existsSync(path.join(dir, ".git")));
}

test("THE INSTALLED WRAPPER IS THE PINNED COMMIT, byte for byte", (t) => {
  const pin = pinnedWrapperSha(readFileSync(path.join(BRIDGE, "package.json"), "utf8"));
  assert.match(pin, /^[0-9a-f]{40}$/, "package.json must pin a full 40-character sha");

  if (!existsSync(INSTALLED)) {
    t.skip("node_modules/aify-wrapper is absent, so the installed bytes went unread: run "
      + "`npm install` in mcp/stdio");
    return;
  }
  const repo = upstreamCheckout();
  if (!repo) {
    t.skip("no aify-wrapper checkout (AIFY_WRAPPER_REPO or ~/projects/aify-wrapper), so the "
      + `bytes of ${pin.slice(0, 7)} could not be read and NOTHING was compared`);
    return;
  }
  let names;
  try {
    names = execFileSync("git", ["ls-tree", "--name-only", "-r", pin], { cwd: repo, encoding: "utf8" })
      .split("\n").map((n) => n.trim()).filter(Boolean);
  } catch {
    t.skip(`the checkout at ${repo} does not hold ${pin.slice(0, 7)}, so nothing was compared: `
      + "fetch it and re-run");
    return;
  }

  const differing = [];
  const compared = [];
  for (const name of names) {
    const local = path.join(INSTALLED, name);
    if (!existsSync(local)) continue;        // npm does not publish this file; not a finding
    const upstream = execFileSync("git", ["show", `${pin}:${name}`], { cwd: repo, maxBuffer: 1 << 26 });
    compared.push(name);
    if (!readFileSync(local).equals(upstream)) differing.push(name);
  }

  // THE CONTROL. An empty comparison satisfies a byte-identity assertion completely, so the count
  // and the SUBJECT are both asserted -- the templates are what the pin is for.
  assert.ok(compared.length > 0,
    `nothing was compared: ${names.length} files at ${pin.slice(0, 7)}, none of them present in `
    + "node_modules/aify-wrapper, so this test would pass on an empty directory");
  for (const template of TEMPLATES) {
    assert.ok(compared.includes(template),
      `${template} was not compared, so the artifact this pin exists for went unchecked`);
  }

  assert.deepEqual(differing, [],
    `node_modules/aify-wrapper does not hold the bytes of ${pin.slice(0, 7)}. npm reports success `
    + "on an install that changes nothing when the tree matches the lock it was handed. Remove "
    + "node_modules/aify-wrapper and reinstall.");
});
