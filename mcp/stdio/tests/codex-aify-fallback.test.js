// Smoke test: ensure the codex-aify wrapper installed by install.sh
// includes the resume-fallback shell guard introduced in Plan 1, and
// the multi-layout session probe introduced in Plan 4 Task 14.
// Pinning a textual marker keeps the regression cheap (we don't actually
// spawn codex; we verify the installed script reflects the intended shape).
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { renderWrapper } from "./wrapper-harness.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// REPOINTED 2026-08-19 (v0.6 Phase 2): this read install.sh's SOURCE and went red when the
// codex-aify body moved into wrappers/codex-aify.sh.in, though the render was proven
// byte-identical. A location pin breaks on a move and stays green on a defect. It now reads the
// RENDERED wrapper — the artifact an operator installs — which a move cannot break and a broken
// render cannot hide from.
const INSTALL_SH = path.join(renderWrapper("codex"), "codex-aify");

test("install.sh codex-aify wrapper contains stale-handle fallback marker", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  // The wrapper-generation block must include the comment + guard so a
  // future refactor can't silently remove the safety net.
  assert.ok(
    src.includes("Plan 1: try-resume, fall back to fresh codex if the saved session")
      || src.includes("Plan 4")
      || src.includes("try-resume"),
    "expected the resume fallback comment in install.sh"
  );
  assert.ok(
    src.includes("CODEX_RESUME_HANDLE"),
    "expected a CODEX_RESUME_HANDLE variable to be parsed in the wrapper"
  );
});

// Pin that install.sh's codex-aify wrapper checks multiple session-storage
// layouts (flat / date-sharded / dir-per-session) — Plan 4 Task 14.
test("install.sh codex-aify wrapper checks date-sharded codex session layout", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  // The wrapper must scan beyond a single flat-file path. Plan 4 Task 14
  // accepts any of: flat, date-sharded (find/recursive), dir-per-session.
  // Look for evidence: either a find command in ~/.codex/sessions OR
  // a multi-path check OR a date-sharded glob.
  const hasFind = /find\s+["']?[\s$]*HOME\/\.codex\/sessions/.test(src)
              || /find\s+["']?\${HOME}\/\.codex\/sessions/.test(src)
              || /find.*\.codex\/sessions/.test(src);
  const hasMultiPath = /CODEX_SESSION_FOUND/.test(src)
                    || /rollout-/.test(src);
  assert.ok(
    hasFind || hasMultiPath,
    "expected install.sh codex-aify wrapper to probe multiple codex session storage layouts (find / multi-path / rollout pattern)"
  );
});

test("install.sh codex-aify wrapper still has CODEX_RESUME_HANDLE parsing", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  assert.ok(/CODEX_RESUME_HANDLE/.test(src), "must preserve --resume handle parsing");
});
