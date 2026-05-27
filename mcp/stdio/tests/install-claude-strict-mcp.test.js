// Pin that install.sh's claude-aify wrapper drops --strict-mcp-config by
// default, and only adds it when AIFY_CLAUDE_STRICT_MCP=1. The legacy
// behavior (always-strict) caused operator pain — wrapper-only claude
// sessions lost access to the operator's full ~/.claude.json MCP server
// list (aify-project-graph, github, browsermcp, etc.).
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(__dirname, "../../../install.sh"), "utf8");

test("install.sh has the env-gate for AIFY_CLAUDE_STRICT_MCP", () => {
  assert.ok(
    /AIFY_CLAUDE_STRICT_MCP/.test(SRC),
    "expected AIFY_CLAUDE_STRICT_MCP env-gate in install.sh"
  );
});

test("install.sh wrapper does NOT unconditionally pass --strict-mcp-config", () => {
  // The new wrapper guards the strict flag behind the env var. If a line
  // unconditionally adds --strict-mcp-config to CLAUDE_MCP_FLAGS without
  // checking AIFY_CLAUDE_STRICT_MCP nearby, that's the old behavior.
  const lines = SRC.split("\n");
  const strictLines = lines
    .map((l, i) => [l, i])
    .filter(([l]) => l.includes("--strict-mcp-config"));
  for (const [line, i] of strictLines) {
    // Look at 5 lines before/after for the env gate
    const window = lines.slice(Math.max(0, i - 5), i + 5).join("\n");
    assert.ok(
      /AIFY_CLAUDE_STRICT_MCP/.test(window),
      `--strict-mcp-config at line ${i + 1} is not guarded by AIFY_CLAUDE_STRICT_MCP env-gate`
    );
  }
});
