// Plan 4 Task 16 — pin that redeploy.sh exists at repo root and contains
// the expected detection + invocation logic.
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../../../");
const REDEPLOY = path.join(ROOT, "redeploy.sh");

test("redeploy.sh exists at repo root", () => {
  assert.ok(fs.existsSync(REDEPLOY), "redeploy.sh must exist at repo root");
});

test("redeploy.sh references *-aify wrappers", () => {
  const src = fs.readFileSync(REDEPLOY, "utf8");
  assert.ok(/-aify/.test(src), "must reference *-aify wrappers");
});

test("redeploy.sh invokes install.sh with --client", () => {
  const src = fs.readFileSync(REDEPLOY, "utf8");
  assert.ok(/install\.sh/.test(src), "must invoke install.sh");
  assert.ok(/--client/.test(src), "must pass --client to install.sh");
});

test("redeploy.sh has a default SERVER_URL fallback", () => {
  const src = fs.readFileSync(REDEPLOY, "utf8");
  // Accept either inline default or a configurable env var
  assert.ok(/SERVER_URL/.test(src) || /AIFY_DEFAULT_SERVER_URL/.test(src),
    "must have a SERVER_URL variable/default");
});
