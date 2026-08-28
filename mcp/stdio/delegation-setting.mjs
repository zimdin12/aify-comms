// Does `AIFY_COMMS_DELEGATE_SPAWNS` mean yes? ONE answer, for every reader of the setting.
//
// There were FOUR readers and TWO truth functions. `env-client.mjs` DECIDES whether a spawn is
// delegated and accepts only an affirmative word; the doctor's launcher parser, the shell reader
// behind `redeploy.sh`, and the launcher's own startup banner all treated ANY non-blank value as on.
// Measured 2026-08-28 over eleven spellings, five disagreed:
//
//     value      decider   reporters
//     "1"        on        on
//     "true"     on        on
//     ""         off       off
//     "0"        off       ON      <-- the natural way to write "off"
//     "false"    off       ON
//     "no"       off       ON
//     "off"      off       ON
//     "maybe"    off       ON      <-- a typo reads as a decision
//
// So an operator who turned delegation off the obvious way got spawns running locally (right) while
// `aify-comms doctor` reported `delegated` and went on to probe aify-env -- failing `unreachable` for
// a setting that was not in effect. The launcher's banner told them the same untruth on every start.
// That is precisely the failure `spawn-delegation` exists to prevent: an instrument disagreeing with
// the thing it measures.
//
// The gate that was supposed to keep these in step compared the two files' REGEX SPELLING, which is a
// proxy for agreement and not agreement. Both spelled the pattern identically and answered
// differently. `where-spawns-run-has-one-parser.test.js` now compares VERDICTS over a value table,
// and the shell reader -- which cannot import this -- is held to the same table.

/** Only these mean yes. "0" and "false" are what somebody types when they mean off. */
export const AFFIRMATIVE = ["1", "true", "yes", "on"];

const AFFIRMATIVE_SET = new Set(AFFIRMATIVE);

/**
 * Is this value an opt-in?
 *
 * Absence, blankness and every negative spelling are off. A value nobody declared -- `maybe`, a typo,
 * a half-finished edit -- is also off, because turning a host's spawning over to another daemon on
 * the strength of an unrecognised string is the more expensive way to be wrong.
 *
 * @param {unknown} value the raw setting, from a launcher file or an environment
 */
export function delegationOptedIn(value) {
  return AFFIRMATIVE_SET.has(String(value ?? "").trim().toLowerCase());
}
