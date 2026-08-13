import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "server.js"), "utf8");
const main = source.match(/async function main\(\) \{([\s\S]*?)\n\}/)?.[1] || "";

test("Codex live discovery cannot block MCP startup", () => {
  // Auto-registration reaches codex live discovery, which talks to an app-server that may not answer. If
  // `main()` awaited it, MCP startup would block behind that — the client would sit with no tools while a
  // discovery call timed out.
  //
  // THIS USED TO MATCH THE FUNCTION'S NAME (`await autoRegisterConfiguredAgent()` / `…().catch`). When the
  // function moved to `auto-registration.mjs` and the call became `makeAutoRegister({…})().catch(…)`, the
  // positive assertion failed — and, worse, the NEGATIVE one started passing for free, because a regex
  // looking for a name that no longer appears can never match. The invariant is "not awaited", so it is now
  // asserted against whatever the call actually is.
  const call = main.match(/^.*makeAutoRegister\(.*$/m)?.[0] || "";
  assert.ok(call, "main() must still kick off auto-registration");
  assert.doesNotMatch(call, /(?<![\w])await(?![\w])/, "auto-registration must not be awaited in main()");
  assert.match(call, /\.catch\(/, "…and must handle its own rejection, or it becomes an unhandled one");

  // Positive control for the negative above: `await` IS detectable in this text, so the assertion is not
  // passing merely because nothing in `main()` could ever match.
  assert.match(main, /(?<![\w])await(?![\w])/, "main() does await other things — the negative check is live");
});
