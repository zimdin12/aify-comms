// What the doctor REPORTS, separated from what it checks.
//
// WHY IT IS A MODULE. Importing `doctor.js` runs the doctor -- it is a script, not a library -- so
// anything left inline there can only be tested by executing every check against a live service.
// The verdict-shaping is the part most worth testing and the part least worth running a fleet for,
// which is exactly the split `service-check.mjs` already makes for the `service` check.
//
// THE DEFECT THIS EXISTS TO FIX (D10). `skip()` builds `{ok: true, code: "skipped"}`, and the human
// report has always rendered that honestly: `markFor` answers "–" before it ever looks at `ok`. The
// JSON did not. A consumer keys on `.ok`, saw `true`, and a check that COULD NOT RUN reported
// identically to one that ran and passed. Compounded by D11 -- the doctor resolving the API key only
// from the repo checkout, so every service-reading check skips when run from anywhere else -- that
// is how "I could not ask" became "no bridge is online" in an agent's summary.
//
// DERIVED FROM `code`, NOT FROM A FLAG ON THE PRODUCER, because there are two producers. The
// `skip()` helper is one; predicate modules are the other, and `bridgeCurrentVerdict` returns
// `code: "skipped"` through `add()` without touching `skip()`. A fix applied to the helper alone
// would have left the predicate path still claiming a pass -- the half nobody would have noticed.

/** A check that could not run. The single signal both producers already set. */
export const isSkipped = (check) => check?.code === "skipped";

/**
 * The `--json` envelope, and the counts the human summary needs.
 *
 * `ok` KEEPS ITS MEANING: whether anything FAILED. `--strict` exits on that and must not start
 * exiting on skips -- on Windows `bridge-running` and `agent-identity` always skip, so treating a
 * skip as a failure would turn every ordinary Windows run red. That is a worse lie than the one
 * being fixed, not a stricter one.
 *
 * @param {object[]} checks as the doctor accumulated them
 * @param {{repo?: {dir: string, short: string} | null, serviceUrl?: string}} context
 */
export function buildReport(checks, { repo = null, serviceUrl = "" } = {}) {
  const all = Array.isArray(checks) ? checks : [];
  const failed = all.filter((c) => !c?.ok && !isSkipped(c));
  const skipped = all.filter(isSkipped);
  return {
    ok: failed.length === 0,
    // Counted separately so "nothing failed" can never be read as "everything was checked".
    passed: all.length - failed.length - skipped.length,
    failed: failed.length,
    skipped: skipped.length,
    repo: repo ? { dir: repo.dir, head: repo.short } : null,
    service_url: serviceUrl,
    // `ok: false` on a skipped row, so a consumer keying on `.ok` alone cannot read it as a pass.
    // `markFor` answers on `code` first, so the human glyph is unchanged by this.
    checks: all.map((c) => (isSkipped(c) ? { ...c, ok: false, skipped: true } : c)),
  };
}

/**
 * The closing line of the human report.
 *
 * "All checks passed." was printed after runs where several checks never ran at all. The skipped
 * count is named on its own, in the same words the bridge test runner uses for the same reason: a
 * reader has to be told what was NOT verified, not just that nothing broke.
 */
export function summaryLine(report) {
  const notVerified = report.skipped ? ` ${report.skipped} skipped, so NOT verified here.` : "";
  return report.failed
    ? `  ${report.failed} check(s) need attention.${notVerified}`
    : `  ${report.passed} check(s) passed.${notVerified}`;
}
