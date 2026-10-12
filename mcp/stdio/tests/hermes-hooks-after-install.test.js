// What install.sh does to hermes' hooks once written (scripts/hermes-hooks-after-install.cjs): each aify hook listed
// once per event, and an approved hook carried to its new Git Bash command. A managed hermes never shows the approval
// prompt, so a command whose approval is not carried stops running.

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { carried, currentCommands, withoutDuplicateHooks } = require("../../../scripts/hermes-hooks-after-install.cjs");

const GIT = '"C:/Program Files/Git/usr/bin/bash.exe"';
const config = [
  "hooks:",
  "  pre_llm_call:",
  `    - command: ${JSON.stringify(`${GIT} "C:\\h\\agent-hooks\\aify-turn-start.sh"`)}`,
  "  on_session_end:",
  `    - command: ${JSON.stringify(`${GIT} "C:\\h\\agent-hooks\\aify-turn-end.sh"`)}`,
  "  post_tool_call:",
  `    - command: ${JSON.stringify("python other-tool.py")}`,
].join("\n");

test("THE CURRENT COMMAND of each aify hook is read from config.yaml, and nothing else is", () => {
  assert.deepEqual(Object.keys(currentCommands(config)).sort(), ["aify-turn-end.sh", "aify-turn-start.sh"]);
});

test("AN APPROVED HOOK keeps its approval under its new command, for the same event only", () => {
  const approvals = [
    { event: "pre_llm_call", command: 'bash "C:\\h\\agent-hooks\\aify-turn-start.sh"' },
    { event: "post_tool_call", command: "python other-tool.py" },
  ];
  const added = carried(approvals, currentCommands(config), "now", () => "mtime");
  assert.deepEqual(added, [{ approved_at: "now", command: `${GIT} "C:\\h\\agent-hooks\\aify-turn-start.sh"`,
    event: "pre_llm_call", script_mtime_at_approval: "mtime" }]);
});

test("A HOOK NEVER APPROVED gets no approval, and one already carried is not added twice", () => {
  const approvals = [{ event: "pre_llm_call", command: `${GIT} "C:\\h\\agent-hooks\\aify-turn-start.sh"` }];
  assert.deepEqual(carried(approvals, currentCommands(config), "now", () => null), []);
  assert.deepEqual(carried([], currentCommands(config), "now", () => null), [], "turn-end was never approved");
});

test("EACH AIFY HOOK IS LISTED ONCE PER EVENT, and the operator's own hooks and other events are untouched", () => {
  const cmd = (script) => JSON.stringify(`${GIT} "C:\\h\\agent-hooks\\${script}"`);
  const config = [
    "model: x",
    "hooks:",
    "  pre_llm_call:",
    `    - command: ${cmd("aify-turn-start.sh")}`,
    "      timeout: 3",
    "    - matcher: .*",
    `      command: ${cmd("aify-turn-start.sh")}`,
    "      timeout: 3",
    `    - command: ${JSON.stringify("python mine.py")}`,
    "      timeout: 3",
    "  post_tool_call:",
    "    - matcher: \".*\"",
    `      command: ${cmd("aify-turn-start.sh")}`,
    "      timeout: 3",
    "hooks_auto_accept: false",
  ].join("\r\n");
  const { text, removed } = withoutDuplicateHooks(config);
  assert.equal(removed, 1);
  assert.equal(text.split("\r\n").length, 15 - 3, "exactly the second turn-start entry went");
  assert.match(text, /mine\.py/);
  assert.match(text, /post_tool_call:\r\n    - matcher: ".*"\r\n      command: .*aify-turn-start/, "another event keeps its own entry");
  assert.match(text, /hooks_auto_accept: false$/);
  assert.equal(withoutDuplicateHooks(text).removed, 0, "a second pass changes nothing");
});
