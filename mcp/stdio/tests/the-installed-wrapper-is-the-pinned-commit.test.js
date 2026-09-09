// The wrapper files in node_modules are the ONES THE PACKAGE PUBLISHES, and they are its BYTES.
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
// THE REQUIRED POPULATION IS DERIVED, NOT INTERSECTED, and the first version of this test got that
// wrong. It compared only files that HAPPENED to be present and treated every absent one as
// unpublished -- so a synthetic installation with `install.sh` deleted passed, and so did one with
// `render.sh` deleted, both of them files the pinned package.json publishes and one of them a `bin`
// target that invokes the other. Review built both. An intersection cannot notice a missing file,
// which is the failure mode of a partial install.
//
// So the population comes from the pinned commit's OWN package.json: every path under a `files[]`
// entry, plus `package.json` itself and every `bin` target. Each must be PRESENT and byte-identical.
// A file in node_modules that the package does not publish is not a finding.
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

//: The artifact the pin exists for. Named explicitly so that a derived population which somehow
//: excluded them would fail loudly rather than quietly shrink.
const TEMPLATES = [
  "wrappers/claude-aify.sh.in",
  "wrappers/codex-aify.sh.in",
  "wrappers/hermes-aify.sh.in",
  "wrappers/pi-aify.sh.in",
];

//: npm publishes these whatever `files[]` says. Listing them keeps the derived set from depending on
//: a manifest field to include the manifest.
const ALWAYS_PUBLISHED = ["package.json"];

function upstreamCheckout() {
  return [
    process.env.AIFY_WRAPPER_REPO,
    path.join(homedir(), "projects", "aify-wrapper"),
  ].find((dir) => dir && existsSync(path.join(dir, ".git")));
}

function show(repo, pin, name) {
  return execFileSync("git", ["show", `${pin}:${name}`], { cwd: repo, maxBuffer: 1 << 26 });
}

/** Every path the pinned commit's own package.json says the package publishes. */
export function requiredPaths(names, manifest) {
  const entries = Array.isArray(manifest.files) ? manifest.files : [];
  const bins = typeof manifest.bin === "string"
    ? [manifest.bin]
    : Object.values(manifest.bin || {});
  const exact = new Set([
    ...ALWAYS_PUBLISHED,
    ...bins.map((b) => String(b).replace(/^\.\//, "")),
    ...entries.filter((e) => !e.endsWith("/")),
  ]);
  const prefixes = entries.filter((e) => e.endsWith("/"));
  return names.filter((n) => exact.has(n) || prefixes.some((p) => n.startsWith(p)));
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
  let manifest;
  try {
    names = execFileSync("git", ["ls-tree", "--name-only", "-r", pin], { cwd: repo, encoding: "utf8" })
      .split("\n").map((n) => n.trim()).filter(Boolean);
    manifest = JSON.parse(show(repo, pin, "package.json").toString("utf8"));
  } catch {
    t.skip(`the checkout at ${repo} does not hold ${pin.slice(0, 7)}, so nothing was compared: `
      + "fetch it and re-run");
    return;
  }

  const required = requiredPaths(names, manifest);

  // THE CONTROLS. An empty or shrunken population satisfies byte-identity completely, so the size
  // and the SUBJECT are both asserted before any comparison is believed.
  assert.ok(required.length >= 10,
    `only ${required.length} published path(s) derived from ${pin.slice(0, 7)}'s package.json, out `
    + `of ${names.length} tracked files -- the manifest's files[] was not read as intended`);
  for (const template of TEMPLATES) {
    assert.ok(required.includes(template),
      `${template} is not in the derived population, so the artifact this pin exists for would go `
      + "unchecked");
  }

  const missing = required.filter((n) => !existsSync(path.join(INSTALLED, n)));
  assert.deepEqual(missing, [],
    `node_modules/aify-wrapper is MISSING file(s) that ${pin.slice(0, 7)} publishes. A partial `
    + "install renders from whatever survived. Remove node_modules/aify-wrapper and reinstall.");

  const differing = required.filter(
    (n) => !readFileSync(path.join(INSTALLED, n)).equals(show(repo, pin, n)));
  assert.deepEqual(differing, [],
    `node_modules/aify-wrapper does not hold the bytes of ${pin.slice(0, 7)}. npm reports success `
    + "on an install that changes nothing when the tree matches the lock it was handed. Remove "
    + "node_modules/aify-wrapper and reinstall.");
});
