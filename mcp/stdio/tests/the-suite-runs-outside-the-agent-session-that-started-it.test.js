// run-all.mjs hands each test file an environment without the session and Herdr of the terminal it was
// started from. Started from an agent's Herdr pane, the suite ran real launchers holding that pane's live
// socket and that agent's id (2026-10-03). Green for nothing when run from a plain terminal, which holds
// neither; on this host the suite is run from agent panes.

import assert from "node:assert/strict";
import { test } from "node:test";

test("no test file sees a Herdr or an agent identity it did not set itself", () => {
  const leaked = Object.keys(process.env).filter((name) =>
    /^(AIFY_)?HERDR_/i.test(name) || ["AIFY_AGENT_ID", "AIFY_AGENT_LEASE", "CLAUDE_CODE_CHILD_SESSION"].includes(name.toUpperCase()));
  assert.deepEqual(leaked, []);
});
