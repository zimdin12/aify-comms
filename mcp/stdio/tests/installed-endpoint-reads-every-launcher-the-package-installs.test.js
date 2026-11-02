#!/usr/bin/env node
// scripts/installed-endpoint.sh keeps a TYPED launcher list, on purpose: it runs before `npm install` fetches the
// wrapper package (install.sh's endpoint prompt, redeploy.sh, install-state.sh) and must recover the endpoint from
// launchers already on disk, so it cannot read the package. A typed list goes stale when the package gains a launcher
// or an alias, and a launcher it does not read is an endpoint an update cannot keep. So this holds the list to the
// package's own derivation (lib/launcher-names.mjs: templates sorted, then wrappers/aliases in file order).

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const PACKAGE = path.join(HERE, "..", "node_modules", "aify-wrapper");

test("installed-endpoint.sh reads every launcher the pinned package installs, in the package's order", async () => {
  const { launcherNames } = await import(pathToFileURL(path.join(PACKAGE, "lib", "launcher-names.mjs")).href);
  const script = fs.readFileSync(path.join(REPO, "scripts", "installed-endpoint.sh"), "utf8");
  const loops = [...script.matchAll(/^for name in ([^;\n]+); do\r?$/gm)];
  assert.equal(loops.length, 1, "the reader's launcher loop moved; this test reads it");
  assert.deepEqual(loops[0][1].trim().split(/\s+/), launcherNames(PACKAGE));
});
