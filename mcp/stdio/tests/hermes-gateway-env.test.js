#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const installText = fs.readFileSync(path.join(repoRoot, "install.sh"), "utf8");
const pluginText = fs.readFileSync(
  path.join(repoRoot, "integrations/hermes-aify-plugin/aify_hermes_plugin/patches.py"),
  "utf8",
);

assert.match(
  pluginText,
  /os\.environ\["AIFY_HERMES_GATEWAY_URL"\] = gateway_url/,
  "Hermes plugin must publish the current dashboard gateway URL",
);
assert.doesNotMatch(
  pluginText,
  /if os\.environ\.get\("AIFY_HERMES_GATEWAY_URL", ""\)\.strip\(\):\s*return/s,
  "Hermes plugin must not preserve inherited stale gateway URLs",
);

for (const name of [
  "AIFY_HERMES_GATEWAY_URL",
  "HERMES_TUI_GATEWAY_URL",
  "AIFY_HERMES_GATEWAY_TOKEN",
  "AIFY_HERMES_GATEWAY_TOKEN_ENV",
]) {
  assert.match(
    installText,
    new RegExp(`unset ${name}`),
    `bash hermes-aify wrapper should clear stale ${name} before dashboard start`,
  );
  assert.match(
    installText,
    new RegExp(`Remove-Item Env:${name} -ErrorAction SilentlyContinue`),
    `PowerShell hermes-aify wrapper should clear stale ${name} before dashboard start`,
  );
}

console.log("hermes-gateway-env.test.js: all assertions passed");
