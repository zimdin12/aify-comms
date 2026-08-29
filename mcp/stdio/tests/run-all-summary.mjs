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

/**
 * Where the wall time went. Pure, so the number in a report comes from the same code that printed it.
 *
 * WHY THIS EXISTS. The runner spawns one node process per file, serially, and reported only pass or
 * fail -- so "the tests take a long time" had no shape to it and no way to argue about which files
 * were worth their cost. Measured 2026-08-29: `claude-wrapper-behaviour.test.js` alone is 192 seconds
 * for 18 tests. It renders real launchers and runs them, so that is not waste; it IS the file that
 * would have caught the unsubstituted `@@SERVICE_NAME@@`. But a suite cannot be tiered by a feeling
 * about which files are slow, and a share is the number that decides: a file holding 30% of the run
 * is a tiering decision, and one holding 0.2% is noise however long it feels.
 *
 * SHARE OF WALL TIME, not of file count. The distribution here is extremely skewed and a mean would
 * hide that.
 *
 * @param {{file: string, ms?: number}[]} results
 * @param {number} [limit]
 * @returns {{totalMs: number, ranked: {file: string, ms: number, share: number}[], headShare: number}}
 */
export function slowest(results, limit = 15) {
  const timed = (results ?? []).filter((r) => Number.isFinite(Number(r?.ms)));
  const totalMs = timed.reduce((sum, r) => sum + Number(r.ms), 0);
  const ranked = timed
    .slice()
    .sort((a, b) => Number(b.ms) - Number(a.ms))
    .slice(0, Math.max(0, limit))
    .map((r) => ({
      file: r.file,
      ms: Math.round(Number(r.ms)),
      // A share of nothing is 0, never NaN: a runner that timed no file must print a number a reader
      // can read as "none of it", not a hole where the instrument failed.
      share: totalMs > 0 ? Number(r.ms) / totalMs : 0,
    }));
  const headShare = totalMs > 0
    ? ranked.reduce((sum, r) => sum + r.ms, 0) / totalMs
    : 0;
  return { totalMs: Math.round(totalMs), ranked, headShare };
}
