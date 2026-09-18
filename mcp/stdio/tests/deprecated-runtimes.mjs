// Test files for a DEPRECATED runtime are kept, and not run by default.
//
// Pi is deprecated (2026-09-18): the operator no longer uses it but keeps the support in case it
// becomes worth using again, so its tests are kept as the proof to revive rather than deleted. A
// file opts in with one line near its top:
//
//     // deprecated-runtime: pi
//
// and runs only when AIFY_TEST_DEPRECATED names that runtime (`pi`, or `all`). The runner lists every
// file it leaves out, so a disabled file is never counted as a pass.

import { readFileSync } from "node:fs";

const MARKER = /^\s*(?:\/\/|#)\s*deprecated-runtime:\s*([a-z0-9_-]+)\s*$/m;
const HEAD_CHARS = 2000;

/** The deprecated runtime a test file declares, or "" if it declares none. */
export function deprecatedRuntimeOf(source) {
  const match = MARKER.exec(String(source || "").slice(0, HEAD_CHARS));
  return match ? match[1] : "";
}

/** Whether a file declaring `runtime` should run, given AIFY_TEST_DEPRECATED. */
export function deprecatedRuntimeEnabled(runtime, setting = process.env.AIFY_TEST_DEPRECATED) {
  if (!runtime) return true;
  const wanted = String(setting || "").split(",").map((part) => part.trim().toLowerCase()).filter(Boolean);
  return wanted.includes("all") || wanted.includes(runtime);
}

/** The deprecated runtime that disables `path` in this run, or "" when it runs. */
export function disabledBy(path, setting = process.env.AIFY_TEST_DEPRECATED) {
  const runtime = deprecatedRuntimeOf(readFileSync(path, "utf8"));
  return deprecatedRuntimeEnabled(runtime, setting) ? "" : runtime;
}
