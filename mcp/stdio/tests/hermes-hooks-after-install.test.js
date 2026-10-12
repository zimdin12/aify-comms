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
  assert.deepEqual(Object.keys(currentCommands(config)).sort(),
    ["c:/h/agent-hooks/aify-turn-end.sh", "c:/h/agent-hooks/aify-turn-start.sh"]);
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

test("AN APPROVAL FOR ANOTHER FILE OF THE SAME NAME is not carried, and that file's entry is not folded into ours", () => {
  // Review of 0.9.1 R4: both matched on the basename alone.
  const elsewhere = { event: "pre_llm_call", command: 'bash "D:\\mine\\aify-turn-start.sh"' };
  assert.deepEqual(carried([elsewhere], currentCommands(config), "now", () => null), []);
  const two = [
    "hooks:",
    "  pre_llm_call:",
    `    - command: ${JSON.stringify(`${GIT} "C:\\h\\agent-hooks\\aify-turn-start.sh"`)}`,
    `    - command: ${JSON.stringify('bash "D:\\mine\\aify-turn-start.sh"')}`,
  ].join("\n");
  assert.equal(withoutDuplicateHooks(two).removed, 0);
});

test("ONLY THE INSTALLER'S OWN COMMAND FORM counts, and a POSIX path keeps its case", () => {
  // Review of 0.9.1 R4, second pass: an approval moved to a command with a prefix, and two case-distinct POSIX files were
  // folded into one.
  const prefixed = [
    "hooks:",
    "  pre_llm_call:",
    `    - command: ${JSON.stringify('printf unapproved; bash "/h/agent-hooks/aify-turn-start.sh"')}`,
  ].join("\n");
  const approved = [{ event: "pre_llm_call", command: 'bash "/h/agent-hooks/aify-turn-start.sh"' }];
  assert.deepEqual(carried(approved, currentCommands(prefixed), "now", () => null), []);
  const cased = [
    "hooks:",
    "  pre_llm_call:",
    `    - command: ${JSON.stringify('bash "/op/One/agent-hooks/aify-turn-start.sh"')}`,
    `    - command: ${JSON.stringify('bash "/op/one/agent-hooks/aify-turn-start.sh"')}`,
  ].join("\n");
  assert.equal(withoutDuplicateHooks(cased).removed, 0);
  const moved = [{ event: "pre_llm_call", command: 'bash "/op/One/agent-hooks/aify-turn-start.sh"' }];
  const current = currentCommands(`hooks:\n  pre_llm_call:\n    - command: ${JSON.stringify('"/usr/bin/bash" "/op/one/agent-hooks/aify-turn-start.sh"')}`);
  assert.deepEqual(carried(moved, current, "now", () => null), []);
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
