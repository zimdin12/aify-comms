import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../server.js", import.meta.url), "utf8");

test("dashboard terminal input is raw even while Claude shows a prompt", () => {
  const inputBranch = source.slice(
    source.indexOf('} else if (control.action === "input")'),
    source.indexOf('} else if (control.action === "resize")'),
  );

  assert.doesNotMatch(inputBranch, /prepareClaudeTerminalInput|sleep\(/);
  assert.match(inputBranch, /TERMINAL_MANAGER\.input\(terminalId, rawBody\)/);
});
