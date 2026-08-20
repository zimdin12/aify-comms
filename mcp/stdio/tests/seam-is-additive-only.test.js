#!/usr/bin/env node
// v0.6 Phase 8, item 4: proving that with delegation OFF, the spawn path is what it was before the seam.
//
// The claim the operator has to believe before flipping anything is "default-off changed nothing".
// Seven tests already pin the BRANCH -- that `isEnabled()` false means the next two lines run exactly
// as before. That is half the claim. The other half is that nothing ELSE moved while the seam was
// being added, and no branch test can see that: a test of a branch reads the branch.
//
// So this reconstructs each pre-seam file from the current one by removing the blocks the seam
// DECLARES it added, and requires byte-identity with a tracked fixture. It is the same argument
// `extraction-proof.test.mjs` makes for app.js, applied to an insertion rather than an extraction.
//
// Composed with the branch tests it completes the claim: the files are pre-seam plus declared blocks,
// and those blocks are inert when the flag is off. Output batching, auto-answer, the console keepalive
// and the heal path are therefore untouched BY CONSTRUCTION -- a stronger statement than re-testing
// each of them, and a cheaper one to keep true.
//
// BOTH FILES, because the production path is two. `terminal-runtime.js` holds the guard and
// `terminal-manager.mjs` is the call site that supplies the dependency -- and the call site is exactly
// where this seam has already been wrong once, when the flag was a placebo because production omitted
// the argument. Proving only the guard would have proven the half that was never broken.
//
// WHEN THE FLAG IS FLIPPED, RETIRE THIS FILE. It pins a phase, not an invariant: once delegation
// actually delegates, "identical to pre-seam" stops being the property anyone wants.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * Read a file with line endings normalised.
 *
 * NOT a convenience. `terminal-manager.mjs` is CRLF in this working tree and `terminal-runtime.js` is
 * LF, and git rewrites both according to whatever `core.autocrlf` the checkout has -- so a fixture
 * captured here would be red on a colleague's machine for a reason having nothing to do with the seam.
 * Pinning the bytes of a line ending would test the checkout, not the code.
 *
 * It cost a real failure to find: the first version of this file compared raw bytes, passed on the LF
 * file, and reported the CRLF one as differing in all 185 lines.
 */
const CRLF = String.fromCharCode(13, 10);
const LF = String.fromCharCode(10);
const read = (path) => readFileSync(path, "utf8").split(CRLF).join(LF);

const live = (name) => read(join(HERE, "..", name));
const fixture = (name) => read(join(HERE, "fixtures", name));

/**
 * What the seam added to each file, verbatim.
 *
 * Declared rather than matched by pattern: a regex would happily absorb a nearby edit and report
 * success, which is the failure this file exists to make impossible. `guard` is the one block whose
 * prose is expected to change, so it is located by its ends instead of quoted whole.
 */
const PLAN = [
  {
    file: "terminal-runtime.js",
    pristine: "terminal-runtime.pre-seam.js",
    blocks: [
      "    envDelegation = null,\n",
      "    // Injected rather than read here, so a test can drive both branches without setting an env var --\n"
      + "    // and so the default really is \"whatever the environment says\", which is off.\n"
      + "    this.envDelegation = envDelegation;\n",
    ],
    guard: {
      open: "    // v0.6 Phase 8: the seam where spawning leaves aify-comms.\n",
      within: "See docs/PHASE8_STATUS.md.",
      close: "    }\n",
    },
  },
  {
    file: "terminal-manager.mjs",
    pristine: "terminal-manager.pre-seam.mjs",
    blocks: [
      "import { isEnabled } from \"./env-client.mjs\";\n",
    ],
    guard: {
      open: "  // WIRED, because for a while it was not.",
      within: "envDelegation: { isEnabled: () => isEnabled(process.env) },",
      close: "\n",
    },
  },
];

/** Removes one declared block, insisting it occurs exactly once. */
function stripBlock(text, block, where) {
  const count = text.split(block).length - 1;
  assert.equal(count, 1, `a declared seam block appears ${count} times in ${where}, expected once`);
  return text.replace(block, "");
}

/** Removes a block located by its ends, so the prose between them may be edited freely. */
function stripGuard(text, guard, where) {
  const start = text.indexOf(guard.open);
  assert.notEqual(start, -1, `the seam guard's opening line is gone from ${where}`);
  const middle = text.indexOf(guard.within, start);
  assert.notEqual(middle, -1, `the guard in ${where} no longer contains its defining line`);
  const close = text.indexOf(guard.close, middle);
  assert.notEqual(close, -1, `the guard's closing line is gone from ${where}`);
  return text.slice(0, start) + text.slice(close + guard.close.length);
}

for (const entry of PLAN) {
  test(`${entry.file}: removing the declared seam blocks gives back the pre-seam file exactly`, () => {
    let text = live(entry.file);
    for (const block of entry.blocks) text = stripBlock(text, block, entry.file);
    text = stripGuard(text, entry.guard, entry.file);

    assert.equal(
      text,
      fixture(entry.pristine),
      `the seam changed something in ${entry.file} outside the blocks it declared -- re-read the diff `
      + "before editing the fixture, because the fixture is the evidence and not the thing under test",
    );
  });

  test(`${entry.file}: the fixture is a real PRE-seam file`, () => {
    // A positive control. An empty or half-written fixture would make the test above pass for the
    // wrong reason if the live file were ever similarly broken, and a proof that cannot fail is decor.
    const pristine = fixture(entry.pristine);
    assert.ok(pristine.length > 5_000, `${entry.pristine} is ${pristine.length} bytes, far too small`);
    assert.doesNotMatch(
      pristine,
      /envDelegation/,
      `${entry.pristine} already contains the seam, so it is not a pre-seam file`,
    );
  });

  test(`${entry.file}: the live file really does carry the seam`, () => {
    // The negative control. If someone deleted the seam entirely, reconstruction would trivially
    // succeed and this file would report that all was well.
    assert.match(live(entry.file), /envDelegation/);
  });
}
