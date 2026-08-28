// The `service` check: is the running container the build this checkout describes?
//
// EXTRACTED FROM doctor.js SO IT CAN BE EXECUTED. `doctor.js` runs its whole sequence at module scope
// and ends in `process.exit()`, so importing it RUNS the doctor -- which meant this function could only
// ever be asserted ABOUT, never called. That is exactly how its no-checkout early return came to bypass
// `serviceVerdictFrom` entirely: on a host with no repo the verdict was never consulted, an env-supplied
// build identity read as healthy, and every test in the suite passed because none of them could reach
// the call site.
//
// A main-guard on doctor.js would also have worked and is worse: the CLI is reached through a .cmd shim
// on Windows, and a guard that mis-resolves `process.argv[1]` makes the doctor silently do NOTHING.
// Moving the logic to a module that does not self-execute buys the same testability with none of that.
//
// Every collaborator is a parameter with no default, so a caller cannot accidentally get the module's
// own network or the operator's own checkout.
import {
  SERVICE_RUNTIME_EXCLUDE_PATHS,
  SERVICE_RUNTIME_PATHS,
  serviceVerdictFrom,
} from "./doctor-predicates.js";

/**
 * @param deps.get     fetch a JSON path from the service, or null when it does not answer
 * @param deps.add     record a check result
 * @param deps.sh      run a command and return stdout
 * @param deps.repo    the checkout to compare against, or null when there is none
 * @param deps.serverUrl only for the unreachable message
 */
export async function checkService({ get, add, sh, repo, serverUrl }) {
  const health = await get("/health");
  if (!health) {
    return add("service", false, "unreachable",
      `No healthy service at ${serverUrl}.`,
      "Start it: `docker compose up -d --build` (in the repo), then re-run.");
  }
  const ver = await get("/version");
  const sha = String(ver?.sha || "");
  // NO EARLY RETURNS. Both the no-checkout and no-sha cases used to be answered here, which meant the
  // verdict -- and every override rule in it -- was skipped on exactly the hosts that have no repo.
  // The payload now always goes through `serviceVerdictFrom`, which owns all of those branches.
  let totalCommits = 0;
  let runtimeCommits = 0;
  if (repo && sha) {
  // Ask whether any commit since the build touched code the service EXECUTES, not merely
  // whether the sha differs — see serviceBuildVerdict for the false red this replaces
  // (a docs-only commit reported "Your service changes are NOT running") and for why the set
  // is runtime paths rather than Dockerfile COPY sources. Same shape as checkNativeBridge
  // below: two `git log` calls, pure unit-tested verdict.
    totalCommits = Number(sh("git", ["rev-list", "--count", `${sha}..HEAD`], repo.dir) || 0);
    runtimeCommits = Number(sh(
      "git",
      [
        "rev-list", "--count", `${sha}..HEAD`, "--",
        ...SERVICE_RUNTIME_PATHS,
        // Tests live under a runtime path but are not runtime — nothing in the image runs pytest.
        ...SERVICE_RUNTIME_EXCLUDE_PATHS.map((p) => `:(exclude)${p}`),
      ],
      repo.dir,
    ) || 0);
  }
  const verdict = serviceVerdictFrom(ver, {
    headSha: repo?.sha || "",
    headShort: repo?.short || "",
    runtimeCommits,
    totalCommits,
  });
  return add("service", verdict.ok, verdict.code, verdict.detail, verdict.fix);
}
