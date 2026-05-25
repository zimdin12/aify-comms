// Smoke test: ensure the codex-aify wrapper installed by install.sh
// includes the resume-fallback shell guard introduced in Plan 1.
// Pinning a textual marker keeps the regression cheap (we don't actually
// spawn codex; we verify the installed script reflects the intended shape).
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";

const INSTALL_SH = path.resolve("install.sh");

test("install.sh codex-aify wrapper contains stale-handle fallback marker", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  // The wrapper-generation block must include the comment + guard so a
  // future refactor can't silently remove the safety net.
  assert.ok(
    src.includes("Plan 1: try-resume, fall back to fresh codex if the saved session"),
    "expected the Plan 1 fallback comment in install.sh"
  );
  assert.ok(
    src.includes("CODEX_RESUME_HANDLE"),
    "expected a CODEX_RESUME_HANDLE variable to be parsed in the wrapper"
  );
});
