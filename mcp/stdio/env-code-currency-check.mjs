// Is the aify-env serving this host RUNNING the code that is on its disk?
//
// THE GAP THIS CLOSES, and it was found by living through it. v0.6.3's whole renderer — the operator
// asked for "our tui stuff really well, that renderer" — sat on a host whose aify-env had loaded its
// modules before those files existed. Every instrument read green. `tier-version` compares VERSIONS,
// and a daemon running stale bytes reports exactly the version a current one does: `0.6.2` either
// way. `env-bridge` says a claimer is registered, `spawn-delegation` says where spawns run,
// `env-processes` compares process lists. None of them can see it.
//
// aify-env ALREADY ANSWERS THIS and nobody was asking. Its `/health` carries `build` — a content
// hash of the package SOURCE it was started from, computed once in its constructor — beside
// `codeOnDisk`, the same hash over the files that are there now. The pair exists precisely so the
// question "do I need to restart to pick up a fix" has an answer. THE SERVICE ALREADY COMPARES
// THE SAME PAIR when it is advertised -- `api_core/code_currency.py`, rendered as a dashboard
// badge -- so the gap this closes is a doctor row, not an absent reader. It also asks the daemon
// DIRECTLY, which matters: on this host the advertised halves arrive EMPTY and that badge
// reads `unknown`.
//
// IT REPORTS AND NEVER ACTS. Restarting aify-env reaps its predecessor's managed workers, which has
// taken this fleet down more than once — so a tool that decided to do it would be a worse defect
// than the one it fixes. The row names the two hashes and stops.
//
// WHAT A HASH MISMATCH DOES AND DOES NOT MEAN. It means the loaded bytes differ from the bytes on
// disk: a restart would change what runs. It does NOT decompose into which files, so it cannot say a
// particular module is missing — a claim of that shape was made from commit dates during this
// version and withdrawn as unsupported. `PackageBuild` walks the package's own files, not Node's
// loaded-module inventory, so the honest name for a mismatch is package-source drift.

/** Doctor's own shape: `{ ok, code, detail, fix }`. */
const verdict = (ok, code, detail, fix = "") => ({ ok, code, detail, fix });

/**
 * Judge one aify-env health body.
 *
 * PURE, so the decision can fail a test rather than only failing on somebody's machine — the reason
 * every predicate in this directory lives apart from the fetch that feeds it.
 *
 * @param {object|null} health  what `GET {endpoint}/health` answered, or null if it did not
 */
export function envCodeCurrencyVerdict(health) {
  if (!health || typeof health !== "object") {
    // NO EVIDENCE IS NOT A PASS. A silent or unreadable aify-env leaves this unanswered, and a green
    // row here would say "the code is current" on the strength of nothing.
    return verdict(false, "unknown", "aify-env did not answer /health, so whether it is running the "
      + "code on its disk is unknown", "start or check aify-env, then re-run this");
  }

  const boot = String(health.build || "").trim();
  const onDisk = String(health.codeOnDisk || "").trim();

  // THE PAIR IS THE EVIDENCE, so a missing half is unknown rather than ok. An older aify-env that
  // predates `codeOnDisk` sends one and not the other, and reading that as agreement would report
  // green on exactly the hosts most likely to be behind.
  if (!boot || !onDisk) {
    return verdict(false, "unknown", "this aify-env does not report both its boot build and its "
      + `code on disk (boot ${boot || "(none)"}, on disk ${onDisk || "(none)"}), so the comparison `
      + "could not be made", "upgrade aify-env to a build that reports both");
  }

  if (boot === onDisk) {
    return verdict(true, "current", `aify-env is running the code on its disk (${boot})`);
  }

  return verdict(false, "stale", `aify-env LOADED ${boot} and its disk now holds ${onDisk}, so it is `
    + "not running the code that is there. Anything changed since it started — including fixes it "
    + "was restarted for — is not in the running process",
    "restart aify-env when its managed workers can be interrupted; supersession reaps the "
    + "predecessor's workers, so this is the operator's call and not a background action");
}

/**
 * Gather the evidence and add the row.
 *
 * SKIPS WHEN THERE IS NO aify-env TO ASK, which is a configuration and not a fault: a host whose
 * spawns are not delegated has no environment tier, and a red row there would fire on a machine set
 * up exactly as intended.
 */
export async function checkEnvCodeCurrency({ add, skip, fetchJson, endpoint }) {
  if (!endpoint) {
    return skip("env-code-currency",
      "spawns are not delegated, so there is no aify-env whose code could be stale");
  }
  const health = await fetchJson(`${endpoint}/health`);
  const result = envCodeCurrencyVerdict(health);
  return add("env-code-currency", result.ok, result.code, result.detail, result.fix);
}
