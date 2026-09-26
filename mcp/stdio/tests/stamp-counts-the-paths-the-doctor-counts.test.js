// `scripts/stamp.sh` marks a build dirty from the same paths `aify-comms doctor`'s `service` check
// counts as runtime. It repeats that list as a git pathspec, and nothing held the two together, so a
// path added to the doctor's list would not make a build dirty and a dirty build would read clean
// (v0.7.1 review, B5). This reads the pathspec out of stamp.sh's own `git status` line and compares it
// with the doctor's exported lists, whole.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { SERVICE_RUNTIME_EXCLUDE_PATHS, SERVICE_RUNTIME_PATHS } from "../doctor-predicates.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

/** The pathspec stamp.sh hands `git status --porcelain --`, split into includes and excludes. */
function stampPathspec(script) {
  const joined = script.replace(/\\\r?\n\s*/g, " ");
  const match = /git -C "\$REPO_ROOT" status --porcelain -- (.*?) 2>\/dev\/null/.exec(joined);
  assert.ok(match, "stamp.sh no longer has the status line this test reads");
  const includes = [];
  const excludes = [];
  for (const token of match[1].trim().split(/\s+/)) {
    const exclude = /^'?:\(exclude\)(.*?)'?$/.exec(token);
    if (exclude) excludes.push(exclude[1]);
    else includes.push(token.replace(/^'|'$/g, ""));
  }
  return { includes, excludes };
}

test("stamp.sh counts exactly the paths the doctor counts", () => {
  const { includes, excludes } = stampPathspec(readFileSync(path.join(REPO, "scripts", "stamp.sh"), "utf8"));
  assert.deepEqual([...includes].sort(), [...SERVICE_RUNTIME_PATHS].sort());
  assert.deepEqual([...excludes].sort(), [...SERVICE_RUNTIME_EXCLUDE_PATHS].sort());
});

test("control: the reader sees a path that differs", () => {
  const drifted = `  changed="$(git -C "$REPO_ROOT" status --porcelain -- service mcp Dockerfile \\\n    ':(exclude)service/tests' 2>/dev/null)"`;
  const { includes, excludes } = stampPathspec(drifted);
  assert.deepEqual(includes, ["service", "mcp", "Dockerfile"]);
  assert.deepEqual(excludes, ["service/tests"]);
  assert.notDeepEqual([...includes].sort(), [...SERVICE_RUNTIME_PATHS].sort());
});
