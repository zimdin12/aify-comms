#!/usr/bin/env node
// `DelegatedManagedController` exists TWICE, byte-identical, and must stay that way or become one.
//
// `controllers/codex-controller.js` and `controllers/hermes-controller.js` each declare a private class
// of that name — same comment, same body, 29 lines each — and neither imports the other. It is the
// controller returned for managed-via-wrapper dispatch, where the wrapper's child bridge owns the real
// work: it resolves immediately with `status: "delegated"` and exposes no-op interrupt/steer.
//
// HOW IT HID. Two gates that should have caught it could not see a class. `declaringModules` matched
// `function` and `const|let|var` only, so its "exactly one owner" answer was structurally unable to
// mention either copy; and the fork scan that swept 57 duplicated bridge names worked from the same
// blind spot. Both were fixed on 2026-08-16, and this file is what the fix found.
//
// WHY AN AGREEMENT TEST RATHER THAN A MERGE. This repo's standing rule is that a duplication finding
// becomes an agreement test, not a forced refactor — the same call already made for `createDeferred`
// and the turn-busy reporting family. Consolidating these two into a shared module is a source change
// to live controller code on the managed-via-wrapper delivery path, and that is a reviewer's decision.
// What must not happen meanwhile is the pair drifting: a fix applied to the codex copy and not the
// hermes one would change delegated behaviour for one runtime only, and nothing would report it.
//
// If they are ever deliberately made to differ, this test is the place to say so and why.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { declarationSpan } from "../../../service/new_dashboard/extraction-proof.mjs";
import { declaringModules } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const NAME = "DelegatedManagedController";
const OWNERS = ["controllers/codex-controller.js", "controllers/hermes-controller.js"];

const spanIn = (relative) => {
  const source = readFileSync(path.join(STDIO, relative), "utf-8").replace(/\r\n/g, "\n");
  const span = declarationSpan(source, NAME);
  assert.ok(span, `${NAME} not found in ${relative}`);
  return span;
};

// ── the two copies agree, byte for byte ──────────────────────────────────────────────────────
{
  const [codex, hermes] = OWNERS.map(spanIn);
  assert.equal(
    codex.text,
    hermes.text,
    `the two ${NAME} copies have DIVERGED. A fix applied to one changes delegated managed-via-wrapper `
      + "behaviour for that runtime only, silently. Apply it to both, or consolidate them and delete "
      + "this test.",
  );
  assert.equal(codex.end - codex.start + 1, 29, "the class changed size; re-read both before editing");
}

// ── the duplication is exactly two, and where we think it is ─────────────────────────────────
{
  const declared = declaringModules(NAME);
  assert.deepEqual(
    declared.map((d) => d.file).sort(),
    [...OWNERS].sort(),
    "a THIRD copy appeared, or one moved. Each additional copy is another place a fix can miss.",
  );
  for (const entry of declared) {
    assert.equal(entry.kind, "class", "declaringModules must report these as classes, not as bindings");
  }
}

// ── anti-vacuity: the detector can see a class at all ────────────────────────────────────────
{
  // `declaringModules` returned [] for every class in the bridge until the pattern was added. A test
  // asserting "exactly these two files" would have passed just as happily against an empty list if it
  // had been written with `.length <= 2`, so the count is asserted as an exact set above, and here we
  // prove the underlying detector is not simply blind.
  assert.ok(declaringModules(NAME).length > 0, "declaringModules cannot see classes again");
  assert.deepEqual(
    declaringModules("PiSession").map((d) => d.file),
    ["pi-session.js"],
    "a single-owner class must resolve to exactly its one module",
  );
  assert.deepEqual(declaringModules("NoSuchClassAnywhere"), [], "and an absent name to nothing");
}

console.log("delegated-managed-controller-agreement.test.js: all assertions passed");
