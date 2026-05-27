// Plan 4 Task 15: pin that install.sh's claude-aify wrapper-generation
// has the AIFY_CLAUDE_STRICT_MCP env-gate (drops --strict-mcp-config by
// default; only adds it when the env is set). Option A from earlier in
// the session — fix lives at commit 6b79dd0.
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INSTALL_SH = path.resolve(__dirname, "../../../install.sh");

test("install.sh claude-aify generation has AIFY_CLAUDE_STRICT_MCP env-gate", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  assert.ok(/AIFY_CLAUDE_STRICT_MCP/.test(src),
    "expected AIFY_CLAUDE_STRICT_MCP env-gate in install.sh (Option A — drops strict-mcp default)");
});

test("install.sh does NOT unconditionally inject --strict-mcp-config", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  const strictLines = src.split("\n").map((l, i) => ({ l, i }))
    .filter(({ l }) => l.includes("--strict-mcp-config"));
  for (const { l, i } of strictLines) {
    const window = src.split("\n").slice(Math.max(0, i - 5), i + 5).join("\n");
    assert.ok(
      /AIFY_CLAUDE_STRICT_MCP/.test(window),
      `--strict-mcp-config at line ${i + 1} is not gated by AIFY_CLAUDE_STRICT_MCP env var`
    );
  }
});

test("install.sh claude-aify consumes managed model and effort env", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  assert.ok(/AIFY_MANAGED_MODEL/.test(src), "expected claude-aify to read AIFY_MANAGED_MODEL");
  assert.ok(/AIFY_MANAGED_EFFORT/.test(src), "expected claude-aify to read AIFY_MANAGED_EFFORT");
  assert.ok(/--model/.test(src), "expected claude-aify to pass --model when managed model is set");
  assert.ok(/--effort/.test(src), "expected claude-aify to pass --effort when managed effort is set");
});
