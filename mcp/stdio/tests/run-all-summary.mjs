// What the suite runner may call a pass, and what it must say out loud.
//
// run-all.mjs judged every file by EXIT STATUS alone. A file whose tests all SKIPPED exits 0, so it
// read as passed while verifying nothing — and `delegated-terminal-against-real-aify-env.test.js`,
// the standing proof that Phase 8's seam reaches a real environment tier, skips itself when the
// aify-env checkout is absent. On any machine but the one it was written on, that proof ran zero tests
// and the runner reported everything green.
//
// Pure, so both the runner and the suite reach the same verdict about what a run actually established.

// node:test's TAP summary. Files using plain top-level assertions print no summary at all, which is
// not a skip: they are a third of this suite and treating a missing line as skipped would cry wolf.
const SKIPPED_LINE = /^# skipped (\d+)$/m;

/** How many tests a file reported as skipped. Absent means zero, not unknown. */
export function skippedFrom(stdout) {
  const match = SKIPPED_LINE.exec(String(stdout ?? ""));
  return match ? Number(match[1]) : 0;
}

/**
 * The run's verdict, and the one line a reader actually reads.
 *
 * Skips are NAMED rather than counted into the pass total. A cross-repo proof that did not run is the
 * single most useful thing this summary can surface, and it is invisible in an exit status.
 */
export function summarise(results) {
  const failed = results
    .filter((r) => r.status !== 0)
    .map(({ file, status }) => ({ file, status }));
  const skipped = results
    .filter((r) => r.status === 0 && r.skipped > 0)
    .map(({ file, skipped: count }) => ({ file, skipped: count }));
  const passed = results.length - failed.length;

  let line;
  if (failed.length > 0) {
    line = `${failed.length} suite(s) FAILED`;
  } else if (skipped.length > 0) {
    const total = skipped.reduce((sum, s) => sum + s.skipped, 0);
    const files = skipped.length === 1 ? "1 file" : `${skipped.length} files`;
    line = `all ${passed} suite(s) passed — ${total} test(s) skipped in ${files}`;
  } else {
    line = `all ${passed} suite(s) passed`;
  }

  return { passed, failed, skipped, line };
}
