// Plan 4 Task 15: pin that install.sh's claude-aify wrapper-generation
// has the AIFY_CLAUDE_STRICT_MCP env-gate (drops --strict-mcp-config by
// default; only adds it when the env is set). Option A from earlier in
// the session — fix lives at commit 6b79dd0.
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";

const INSTALL_SH = path.resolve("install.sh");

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
