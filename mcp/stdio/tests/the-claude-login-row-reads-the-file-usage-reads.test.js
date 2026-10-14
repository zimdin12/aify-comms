// Two readers of `~/.claude/.credentials.json`, in two repos, must read the same file.
//
// `usage-collector.js` here reads it for the quota pool. `aify-env doctor`'s `claude-login` row reads it
// for the login's deadline -- that row lived in this repo's doctor until 0.9.7 (plan D11) and moved to
// the tier that owns host credentials. Two spellings of one location agree until somebody edits one,
// and the symptom would be silent: a row that always says "no claude credentials" on a host whose login
// is about to lapse.
//
// THE ROW'S PATH IS CALLED, from the aify-env checkout (`AIFY_ENV_REPO`, else beside this one). This
// side's reader spells the path inline, so it is read as source.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { siblingCheckout } from "./_sibling-checkout.mjs";

test("the claude-login row and the usage collector read the same file", async () => {
  const sibling = siblingCheckout("aify-env", "lib/claude-login-check.mjs");
  assert.ok(sibling.dir, `no aify-env checkout with lib/claude-login-check.mjs; set AIFY_ENV_REPO: ${sibling.looked}`);
  const { claudeCredentialsPath } = await import(pathToFileURL(join(sibling.dir, "lib/claude-login-check.mjs")));
  assert.equal(claudeCredentialsPath("/home/op"), join("/home/op", ".claude", ".credentials.json"));

  const ours = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "usage-collector.js"), "utf8");
  assert.match(ours, /"\.claude"\s*,\s*"\.credentials\.json"/,
    "usage-collector.js no longer reads ~/.claude/.credentials.json the same way; one of the two moved");
});
