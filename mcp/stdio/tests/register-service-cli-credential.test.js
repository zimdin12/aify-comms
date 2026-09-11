import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { sealedChildEnv } from "./_child-env.mjs";

const cli = fileURLToPath(new URL("../register-service-cli.mjs", import.meta.url));
const installer = readFileSync(new URL("../../../install.sh", import.meta.url), "utf8");
// Execute the real producer block, not a reimplementation or the whole installer. Only its
// external key helpers are stubbed; no key store, installed config, daemon or API is touched.
const producer = installer.match(/^\[ "\$WITH_API_KEY" = true \].*\nRESOLVED_API_KEY=.*\nif \[ -n "\$\{RESOLVED_API_KEY:-\}" \]; then\n[\s\S]*?^fi$/m)?.[0];
assert.ok(producer, "the installer credential producer must be found before it can be exercised");
const other = { endpoint: "http://other.invalid", endpointEnv: ["OTHER_URL"], keyEnv: [],
  mcp: [{ name: "other", command: "node", args: ["other.js"] }], credentialRef: "other.key" };

function exercise(t, { credentialRef, installerMode, expected = "existing.key" }) {
  const root = mkdtempSync(join(tmpdir(), "aify-cli-credential-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const registry = join(root, "services.json");
  writeFileSync(registry, JSON.stringify({ version: 1, services: {
    "aify-comms": { endpoint: "http://old.invalid", endpointEnv: [], keyEnv: [], mcp: [], credentialRef: "existing.key" },
    "other-service": other,
  } }));
  const env = sealedChildEnv({ HOME: root, USERPROFILE: root, AIFY_ROOT: root,
    AIFY_SERVICE_REGISTRY: registry, CLI: cli, NODE_EXE: process.execPath,
    REGISTRY: registry, BRIDGE: root });
  // Windows env names are case-insensitive. Do not inherit an operator's reference.
  for (const name of Object.keys(env)) if (name.toUpperCase() === "CREDENTIAL_REF") delete env[name];
  if (credentialRef !== undefined) env.CREDENTIAL_REF = credentialRef;
  let run;
  if (installerMode) {
    mkdirSync(join(root, "scripts"));
    writeFileSync(join(root, "scripts", "api-key-for-install.sh"),
      installerMode === "no-key" ? "exit 0\n" : "printf '%s' fixture-only\n");
    writeFileSync(join(root, "scripts", "credential-carrier.sh"),
      installerMode === "present" ? "printf '%s' replacement.key\n" :
        installerMode === "failed" ? "exit 1\n" : "exit 0\n");
    run = spawnSync("bash", ["-c", `${producer}\n"$NODE_EXE" "$CLI" "$REGISTRY" http://new.invalid "$BRIDGE"`], {
      env: { ...env, SCRIPT_DIR: root, WITH_API_KEY: "false" }, encoding: "utf8", timeout: 10000,
    });
  } else {
    run = spawnSync(process.execPath, [cli, registry, "http://new.invalid", root], {
      env, encoding: "utf8", timeout: 10000,
    });
  }
  assert.equal(run.error, undefined);
  assert.equal(run.status, 0, run.stderr);
  const result = JSON.parse(readFileSync(registry, "utf8"));
  assert.deepEqual(Object.keys(result.services).sort(), ["aify-comms", "other-service"]);
  assert.deepEqual(result.services["other-service"], other);
  assert.equal(result.services["aify-comms"].endpoint, "http://new.invalid", "the actual CLI must update the entry");
  assert.equal(result.services["aify-comms"].credentialRef, expected);
}

for (const [name, credentialRef] of [["absent", undefined], ["empty", ""], ["whitespace", " \t "]]) {
  test(`actual CLI preserves a published ref when CREDENTIAL_REF is ${name}`, (t) => exercise(t, { credentialRef }));
}
test("actual CLI replaces a published ref with a trimmed nonempty CREDENTIAL_REF", (t) =>
  exercise(t, { credentialRef: " replacement.key \t", expected: "replacement.key" }));
for (const installerMode of ["no-key", "empty", "failed", "present"]) {
  test(`installer producer to actual CLI: ${installerMode}`, (t) => exercise(t, {
    installerMode, expected: installerMode === "present" ? "replacement.key" : "existing.key",
  }));
}
