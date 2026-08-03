// mkdtemp that actually cleans up.
//
// 2026-08-04: 4,391 `aify-*` directories had accumulated in %TEMP% since 2026-05-26. The Python
// side leaked 2 per pytest process; this side leaked 21 per bridge-suite run across 52 call sites
// that all did `mkdtempSync(path.join(os.tmpdir(), "aify-something-"))` and never removed it.
// Individually invisible, and none of them wrong on its own — which is exactly why it ran for
// three months. A helper is the fix: the cleanup cannot be forgotten if creating the directory
// registers it.
//
// Removal is best-effort on exit: a test that leaves a file locked (Windows) must not turn a
// passing suite red over cleanup, so failures are swallowed. Leaking on that path is still
// strictly better than leaking on every path.
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const pending = new Set();
let hooked = false;

export function tmpDir(prefix) {
  const dir = mkdtempSync(path.join(os.tmpdir(), prefix));
  pending.add(dir);
  if (!hooked) {
    hooked = true;
    process.on("exit", () => {
      for (const d of pending) {
        try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ }
      }
    });
  }
  return dir;
}
