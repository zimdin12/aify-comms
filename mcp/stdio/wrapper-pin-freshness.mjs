// Is the aify-wrapper this repo consumes the one it means to consume, and is it missing a fix?
//
// TWO QUESTIONS, DELIBERATELY SEPARATE. Collapsing them was the first version's mistake.
//
//   CONSUMED    is the pin a full sha, do package and lock agree, is that what node_modules holds?
//               Deterministic. Answerable offline, in a clean clone, with no sibling checkout.
//   UPSTREAM    has aify-wrapper since landed a commit that changes what we render?
//               Environment-dependent, ADVISORY, and only answerable where a checkout exists.
//
// THE MISS, 2026-08-29. aify-comms pinned `94b5716`; aify-wrapper's HEAD was `bb56df5`, three commits
// later, and the top one was `fix(claude launcher): stop inheriting another session's child-session
// marker` -- the fix for a defect the operator had been reporting all day. Its own commit message
// ended "NOT DEPLOYED -- this needs install.sh re-run and every wrapper relaunched". install.sh WAS
// re-run on that host. It rendered the old template.
//
// WHY UPSTREAM IS ADVISORY AND NOT AN INVARIANT. A pin deliberately selects a version. "Behind HEAD by
// any template commit" is not the same as stale unless HEAD is declared the desired authority, and
// making every older branch go red whenever aify-wrapper moves defeats independent versioning and
// manufactures exactly the alarm fatigue this check is supposed to avoid. The durable rule is that the
// selected dependency carries every capability this consumer REQUIRES; HEAD recency is a lead, not an
// authority. A capability manifest is the right long-term answer and does not exist yet.
//
// A FALSE GREEN THIS FILE ALREADY PRODUCED, in its first hour. The caller ran `git log pin..HEAD` and
// mapped every failure to "". With a pin that is not an ancestor -- a force-push, a divergent
// checkout, a sha from a branch -- that query FAILS, the commit list is empty, and the verdict read
// `ok: true`, "0 commits ahead, none touching the templates". A confident answer assembled out of no
// evidence, in a check written to catch exactly that. Query outcomes are typed now, and only `ok`
// counts as an answer.

/** @typedef {"ok"|"no-repo"|"pin-not-ancestor"|"query-failed"} UpstreamProbeStatus */

/**
 * Is the pin a full 40-character sha, and does everything that records it agree?
 *
 * A FULL SHA OR NOTHING. npm resolves a short sha and the lockfile records the full one, so the two
 * disagree on sight and any reader comparing them reports a difference that is not one. Observed
 * during the 2026-08-29 bump: package.json said `bb56df5`, the lock said the 40-character form.
 *
 * @param {object} input
 * @param {string} input.packagePin  the sha in package.json, or ""
 * @param {string} input.lockPin     the sha the lockfile resolved, or ""
 * @param {string} input.installedPin the sha node_modules actually holds, or ""
 * @returns {{ok: boolean, code: string, detail: string, fix: string}}
 */
export function consumedPinVerdict({ packagePin = "", lockPin = "", installedPin = "" } = {}) {
  if (!packagePin) {
    return {
      ok: false, code: "unpinned",
      detail: "package.json does not pin aify-wrapper to a full 40-character commit sha.",
      fix: "Pin it. A short sha is resolved by npm and recorded long, so the two never compare equal.",
    };
  }
  const disagree = [];
  if (lockPin && lockPin !== packagePin) disagree.push(`lock ${lockPin.slice(0, 7)}`);
  if (installedPin && installedPin !== packagePin) disagree.push(`node_modules ${installedPin.slice(0, 7)}`);
  if (disagree.length) {
    return {
      ok: false, code: "disagree",
      detail: `package.json pins ${packagePin.slice(0, 7)} but ${disagree.join(" and ")} disagree. `
        + "The bytes that get rendered are the installed ones.",
      fix: "Run `npm install` in mcp/stdio, and check the result rather than the intent.",
    };
  }
  // NEITHER RECORD PRESENT IS NOT AGREEMENT. A caller that could read no lock and no installed tree
  // has compared the pin against nothing, and saying "consistent" would be the false green this file
  // was written to prevent.
  if (!lockPin && !installedPin) {
    return {
      ok: false, code: "unknown",
      detail: `pinned ${packagePin.slice(0, 7)}, and neither the lockfile nor node_modules could be `
        + "read, so nothing was compared against it.",
      fix: "Run `npm install` in mcp/stdio.",
    };
  }
  return { ok: true, code: "ok", detail: `consuming ${packagePin.slice(0, 7)} consistently`, fix: "" };
}

/**
 * Has upstream landed a commit that changes what this repo renders? ADVISORY.
 *
 * @param {object} input
 * @param {UpstreamProbeStatus} input.status  how the probe went. Only "ok" is an answer.
 * @param {string} input.pin
 * @param {string} input.head
 * @param {string[]} input.consumedSurfaceCommits commits touching the paths this repo consumes
 * @param {number} [input.totalCommits]
 * @returns {{ok: boolean, code: string, detail: string, fix: string}}
 */
export function upstreamAdvisory({
  status = "no-repo", pin = "", head = "", consumedSurfaceCommits = [], totalCommits = 0,
} = {}) {
  const short = (value) => String(value || "").slice(0, 7);
  if (status !== "ok") {
    // EVERY NON-ANSWER IS UNKNOWN, and unknown is not ok. `pin-not-ancestor` in particular used to
    // arrive here as an empty commit list and read as a clean bill of health.
    const why = {
      "no-repo": "no aify-wrapper checkout was available",
      "pin-not-ancestor": `the pinned ${short(pin)} is not an ancestor of ${short(head)}, so `
        + '"commits since" has no meaning here',
      "query-failed": "the git query failed",
    }[status] ?? `the probe reported ${status}`;
    return {
      ok: false, code: "unknown",
      detail: `${why}. Nothing was compared.`,
      fix: "Clone aify-wrapper beside this repo, or set AIFY_WRAPPER_REPO, and re-run.",
    };
  }
  if (pin === head) {
    return { ok: true, code: "current", detail: `pinned ${short(pin)} == aify-wrapper HEAD`, fix: "" };
  }
  if (consumedSurfaceCommits.length) {
    return {
      ok: false, code: "behind-consumed-surface",
      detail: `pinned ${short(pin)}, aify-wrapper HEAD is ${short(head)} — `
        + `${consumedSurfaceCommits.length} commit(s) since then changed paths this repo consumes: `
        + consumedSurfaceCommits.join("; "),
      fix: "Bump the pin to a FULL sha and re-run npm install, then re-render the launchers. A bump "
        + "can also bring a template PARAMETER: check that nothing renders with an unsubstituted "
        + "@@NAME@@ afterwards.",
    };
  }
  return {
    ok: true, code: "current-enough",
    detail: `pinned ${short(pin)}; HEAD is ${short(head)} (${Number(totalCommits) || 0} commit(s) `
      + "ahead, none touching the consumed surface)",
    fix: "",
  };
}

/**
 * The paths this repo actually consumes from aify-wrapper.
 *
 * `wrappers/` alone was too narrow and caught today's defect by luck: install.sh renders from
 * `WRAPPER_TEMPLATE_DIR`, and this repo ALSO executes the package's registry CLI and reads its
 * installed-endpoint utilities. A freshness claim scoped to templates is a template claim, not a
 * dependency claim, and should not be named as one.
 */
export const CONSUMED_SURFACE = Object.freeze(["wrappers/", "lib/", "VERSION"]);

/**
 * A pinned sha, read out of a dependency spec. Full shas only.
 * @param {string} text
 * @returns {string}
 */
export function pinnedWrapperSha(text) {
  const match = /"aify-wrapper"\s*:\s*"[^"]*#([0-9a-f]{40})"/.exec(String(text || ""));
  return match ? match[1] : "";
}
