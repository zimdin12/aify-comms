// install.sh carries an approved aify hermes hook to its new Git Bash command (scripts/hermes-hook-approvals.cjs).
// A managed hermes never shows the approval prompt, so a command whose approval is not carried stops running.

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { carried, currentCommands } = require("../../../scripts/hermes-hook-approvals.cjs");

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
