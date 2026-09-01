// The glyph a check gets in the human report.
//
// A FAILURE WORE A NOTE'S GLYPH. The line this replaces read
//
//     c.code === "skipped" ? "–" : c.code === "partial" ? "~" : c.ok ? "✓" : "✗"
//
// which tests `partial` before it ever looks at `ok`. Those two fields answer different questions:
// `partial` says HOW MUCH EVIDENCE was gathered, `ok` says WHAT THE ANSWER WAS, and they vary
// independently. Both combinations exist in this tool right now:
//
//   * `bridgeCurrentVerdict` returns {ok: true, code: "partial"} -- some live bridges reported a
//     build and the rest are too old to. Genuinely benign; `~` is the right mark for it.
//   * `contextWindowVerdict` returns {ok: false, code: "partial"} when the fan-out cap was reached,
//     and its own text says "this row is not a clean result: the agent this check exists to find may
//     be" among the ones never opened. That is a FAILURE, and it rendered as the same `~`.
//
// So an operator scanning the report for `✗` found none, in a run that had failed to answer the
// question it was asked. That is `a2f9e42`'s false green -- no evidence is not a pass -- reappearing
// one layer up, in the rendering rather than in the verdict. The verdicts were right the whole time.
//
// EXTRACTED because importing `doctor.js` RUNS the doctor, so a decision left inline there can only
// be checked by a source regex or by running the whole tool against a live service. This is the same
// reason `service-check.mjs` and `doctor-api-key.mjs` exist.

/** The mark for one check row: "–" skipped, "✗" failed, "~" incomplete but not failing, "✓" clean. */
export function markFor(check) {
  // A SKIP IS NOT A RESULT, and it carries ok:true, so it is answered before either other field.
  if (check?.code === "skipped") return "–";
  // `ok === false` explicitly, not `!check.ok`: a row with no `ok` at all is a malformed check rather
  // than a failing one, and claiming it failed would be inventing a verdict nobody reported.
  if (check?.ok === false) return "✗";
  if (check?.code === "partial") return "~";
  return "✓";
}
