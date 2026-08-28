// The `env-processes` check: is every process aify-env is running one the control plane knows about?
//
// EXTRACTED SO IT CAN BE EXECUTED, for the reason `service-check.mjs` records: `doctor.js` runs its
// whole sequence at module scope and ends in `process.exit()`, so importing it RUNS the doctor. A
// check written inline there can be asserted ABOUT and never CALLED -- which is how that file's
// no-checkout early return came to bypass its own verdict with every test green.
//
// The predicate this drives (`env-process-reconciliation.mjs`) was written and mutation-tested a
// commit before this file existed, and nothing called it. A proven helper with no call site is the
// failure mode this repo has already paid for twice; this is the other half.
//
// Every collaborator is a parameter with no default, so a caller cannot accidentally reach the
// operator's own network, launcher or hostname.

import { envProcessVerdict, reconcileEnvProcesses } from "./env-process-reconciliation.mjs";
import { launcherDelegation } from "./doctor-predicates.js";

/**
 * @param deps.get          fetch a JSON path from the aify-comms service, or null when it does not answer
 * @param deps.add          record a check result
 * @param deps.skip         record a check as not applicable here
 * @param deps.fetchJson    fetch a JSON URL outright (aify-env is not behind `get`'s base URL)
 * @param deps.launcherText the installed environment-bridge launcher, or null when there is none
 * @param deps.machineId    this host's machine id, used to find which environment is ours
 */
export async function checkEnvProcesses({ get, add, skip, fetchJson, launcherText, machineId }) {
  const { on: delegating, endpoint } = launcherDelegation(launcherText);
  if (!delegating || !endpoint) {
    // NOT A PASS AND NOT A FAILURE. With spawns hosted by the bridge itself there is no second list
    // to compare against, so the question does not apply -- and answering `ok` would add a green row
    // for work nobody did.
    return skip("env-processes", "spawns are not delegated, so there is no environment to compare");
  }

  const listing = await fetchJson(`${endpoint}/processes`);
  if (!listing) {
    return add(...toArgs("env-processes", envProcessVerdict({ envAnswered: false })));
  }

  // LIVE ONLY, and the default is live -- but it is passed explicitly because this check's whole
  // meaning depends on it. A listing that included stopped rows would account for processes with
  // terminals that ended, which is precisely the operator's case reported as healthy.
  const terminals = await get("/api/v1/terminals?status=live&limit=500");
  if (!terminals) {
    return add("env-processes", false, "unknown",
      "the service did not answer, so nothing could be compared against what aify-env is running.",
      "Check the `service` row above.");
  }

  const environments = await get("/api/v1/environments");
  // WHICH ENVIRONMENT IS OURS. A doctor run probes the aify-env on ITS host, so phantoms must be
  // scoped to the environment that lives here -- otherwise every other machine's live terminals are
  // reported as missing, which is an alarm that fires on a healthy fleet.
  //
  // An id we cannot determine yields "", and the reconciliation then reports no phantoms at all
  // rather than all of them. Losing one direction is the safe way to be unsure.
  const ours = (environments?.environments ?? []).find(
    (environment) => String(environment?.machineId ?? "") === machineId,
  );

  const result = reconcileEnvProcesses({
    envProcesses: listing.processes ?? (Array.isArray(listing) ? listing : []),
    terminals: terminals.terminals ?? [],
    environmentId: String(ours?.id ?? ""),
  });

  return add(...toArgs("env-processes", envProcessVerdict({
    result,
    envAnswered: true,
    listingTruncated: Boolean(terminals.truncated),
  })));
}

/** A verdict as `add`'s positional arguments. Kept here so the three call sites above stay one line. */
function toArgs(id, verdict) {
  return [id, verdict.ok, verdict.code, verdict.detail, verdict.fix];
}
