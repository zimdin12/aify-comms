// The wrapper harness renders once and isolates every run. Both halves, or neither is safe.
//
// WHY THIS FILE EXISTS. `renderWrapper` now caches: one install.sh run per client per process instead
// of one per test. Measured 2026-08-29, `claude-wrapper-behaviour.test.js` went from 192.4s to 19.0s
// and `claude-wrapper-determinism.test.js` from 65.4s to 21.8s. That saving is only honest while the
// rendered directory is READ-ONLY input.
//
// The first attempt was not. `runWrapper` derived its stub runtime and its sealed HOME from the
// wrapper's own directory, so sharing the render meant sharing the stub -- and
// `claude-wrapper-contract.test.js` went red on its first run: the case asserting a wrapper REFUSES to
// launch on an empty HARNESS_ENDPOINT found a stub an earlier case had installed, and launched.
//
// That is the whole risk of consolidating an expensive fixture: a case that passes because an earlier
// case prepared state for it. It took one run to appear here, and it would not have appeared at all if
// the failing case had happened to be ordered first. So it gets a test rather than a comment.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { renderWrapper, runWrapper } from "./wrapper-harness.mjs";

const SEEDED_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const claudeWrapper = () => path.join(renderWrapper("claude"), "claude-aify");

test("the render is reused within a process", () => {
  // The saving itself. A cache that quietly stopped hitting would cost 9 seconds a call and nothing
  // would report it -- the suite would simply be slow again.
  assert.equal(renderWrapper("claude"), renderWrapper("claude"));
});

test("a different url is a different render", () => {
  // The key has to carry the url. Handing back a launcher baked against another endpoint would be a
  // wrong artifact, not a slow one, which is the worse of the two failures.
  assert.notEqual(renderWrapper("claude"), renderWrapper("claude", { url: "http://127.0.0.2:2" }));
});

test("the rendered directory is not where a run keeps its state", () => {
  const dir = renderWrapper("claude");
  const before = fs.readdirSync(dir).sort();
  runWrapper(claudeWrapper(), { runtimeName: "claude", args: ["--print", "x"] });
  assert.deepEqual(
    fs.readdirSync(dir).sort(), before,
    "a run wrote into the rendered directory, so every later test in the file inherits it",
  );
});

test("WHAT ONE RUN SEEDS, THE NEXT CANNOT SEE", () => {
  // Behavioural, not a path comparison: two runs with the same session id, and only the first has a
  // transcript behind it. If they shared a HOME the second would find the first's file and keep the
  // id, and the branch this asserts would be untestable -- silently, and only in files where some
  // earlier case happened to seed.
  const seeded = runWrapper(claudeWrapper(), {
    runtimeName: "claude",
    env: { CLAUDE_SESSION_ID: SEEDED_ID },
    prepareHome: (home) => {
      const dir = path.join(home, ".claude", "projects", "C--some-workspace");
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, `${SEEDED_ID}.jsonl`), "{}\n");
    },
  });
  assert.equal(seeded.env.CLAUDE_SESSION_ID, SEEDED_ID, "a seeded id must survive — otherwise this "
    + "test proves nothing about the second run either");

  const fresh = runWrapper(claudeWrapper(), {
    runtimeName: "claude",
    env: { CLAUDE_SESSION_ID: SEEDED_ID },
  });
  assert.notEqual(
    fresh.env.CLAUDE_SESSION_ID, SEEDED_ID,
    "the second run saw the first run's transcript, so the sealed HOME is shared and every "
      + "'no transcript exists' case in this suite is passing on borrowed state",
  );
});
