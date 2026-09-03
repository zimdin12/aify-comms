// Pure predicates behind aify-doctor's `env-bridge` check.
//
// NOT ALL PURE SINCE v0.5.4: the two process-inspection readers at the bottom read files, which is why
// they take an injectable reader and a `/proc` root. They are here because the reason the env-bridge
// predicates moved applies to them identically — doctor.js cannot be imported by a test at all. The
// two imports below are the file's FIRST: it had none, and appending a body that used `readFileSync`
// without them left a module that `node --check` parses happily and that throws `ReferenceError` on its
// first real call. That exact failure is recorded in doctor.js's own import block, from the last time
// it happened.
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { delegationOptedIn } from "./delegation-setting.mjs";
// SPAWN CLAIMING MOVED OUT, and is re-exported nowhere -- a stale import must fail loudly rather
// than resolve. `comms_envs` needs the same answer and must not import the doctor to get it.
import { envCanClaimASpawn } from "./spawn-claimer.mjs";
//
// Extracted from doctor.js (v0.2 item B2, done in v0.1) for ONE reason: doctor.js is a top-level
// script that runs every check at import and ends in `process.exit()`, so it cannot be imported by
// a test. That structural fact is why the check with the worst track record in this repo had zero
// unit coverage — and it shipped the same false green twice:
//
//   1. `756f3a5` — the check counted REGISTERED rows and reported "2 connected" with zero bridges
//      alive (one stale 24h, one ~7 weeks).
//   2. review R3/R3a — a FUTURE or unparseable `lastSeen` slipped through the staleness bound, and
//      `degraded` was treated as usable for spawn when the spawn picker requires `online`.
//
// Behaviour here is identical to what doctor.js did before the move; the tests are the new part.
// No I/O, no clock reads except the `now` a caller passes in, so every branch is directly testable.

// ONLINE ONLY — matched to the SPAWN PICKER, which is the thing the env-bridge check claims to
// prove. `api_v2.py` (env selection for a cold start) does `if status.lower() != "online": continue`,
// so a `degraded` environment CANNOT host a managed spawn. An earlier version counted degraded as
// connected, which let doctor read green while no spawn could actually run — the same false-green
// class, one layer along (review R3b). Note the codebase has a THIRD, looser notion: the
// reachability test in api_v2 accepts {online, degraded} when deciding whether an agent is merely
// reachable. That is a different question and is deliberately left alone; "can host a new spawn" is
// the one this check is about.
export const ENV_CONNECTED_STATES = new Set(["online"]);
export const ENV_KNOWN_STATES = new Set(["online", "degraded", "offline", "forgotten", "disabled"]);
// Independent staleness bound. The server derives liveness from `last_seen`, and a bug there is
// exactly how a dead bridge got reported as live twice now (first the row-count check, then
// `degraded` never ageing out because the staleness test was gated on status == "online"). A
// verifier whose whole job is to fail loudly must not depend solely on the value under test — so
// doctor ALSO ages the row itself. Generous vs `environment_offline_seconds` (90s default): this is
// a backstop against a broken derivation, not a second opinion on normal jitter.
export const ENV_STALE_AFTER_MS = 10 * 60 * 1000;

// Bounded tolerance for a stamp in doctor's FUTURE. The service writes `last_seen` from inside the
// CONTAINER; doctor evaluates it on the HOST, and those clocks are not the same clock — measured
// 4.1s apart on this machine, container ahead. With a hard `age >= 0` rule, every heartbeat newer
// than that drift read as bogus, so a bridge beating every few seconds scored EXACTLY the same as
// one dead for 24h and doctor's verdict depended on where in the heartbeat cycle it ran. Live
// 2026-08-03 it printed "No environment bridge is ONLINE" while naming that row `[online, last seen
// ...]` — the false-GREEN class this file exists to prevent, inverted into a false RED, and just as
// bad: doctor is what this repo trusts to prove a deploy took.
//
// R3a's intent is preserved — a bogus far-future stamp still must not green a dead bridge — the
// rejection simply starts past ordinary drift instead of at zero. Sized deliberately: ~7x the
// observed skew, an order of magnitude under ENV_STALE_AFTER_MS, and below the 60s the R3a test
// pins as bogus, so that test keeps failing the values it always failed.
export const ENV_FUTURE_SKEW_MS = 30 * 1000;

export function envLastSeenMs(env) {
  const raw = String(env?.lastSeen || "").trim();
  if (!raw) return NaN;
  return Date.parse(raw.endsWith("Z") || raw.includes("+") ? raw : `${raw}Z`);
}

// Split in two ON PURPOSE — do not merge them back into one function with a defaulted `now`.
// That is what the first version did, and it broke the check immediately: doctor calls
// `list.filter(envIsOnline)`, and `Array.prototype.filter` invokes its callback with
// (element, INDEX, array). The index bound to `now`, so `now - seen` for element 0 was a hugely
// negative age, the `age >= 0` guard rejected it, and a bridge that had beaten 68 seconds ago read
// as not connected. A false RED this time, but the same root shape as the false greens above: a
// predicate whose arity is larger than its callers pass.
//
// So `envIsOnline` takes EXACTLY ONE argument and is safe to hand to filter/map/some; the clock
// is injectable only through `envIsOnlineAt`, which is what the tests drive.
export function envIsOnline(env) {
  return envIsOnlineAt(env, Date.now());
}

export function envIsOnlineAt(env, now) {
  if (!ENV_CONNECTED_STATES.has(String(env?.status || "").trim().toLowerCase())) return false;
  const seen = envLastSeenMs(env);
  // Unparseable or MISSING lastSeen → NOT connected. An earlier version trusted the served status
  // here "rather than invent a failure", but this check exists to fail loudly: a row we cannot date
  // is a row we cannot prove is alive, and every false green in this file so far came from treating
  // unprovable as fine. The detail line names the row so the cause is obvious.
  if (Number.isNaN(seen)) return false;
  // A FUTURE lastSeen must NOT pass (review R3, 2026-07-26): `now - seen` goes negative and
  // trivially satisfies the bound, so a clock-skewed or bogus stamp would green a dead bridge — the
  // very false green this check exists to catch. Rejection starts past ENV_FUTURE_SKEW_MS rather
  // than at zero, because the stamping clock (container) and the judging clock (host) genuinely
  // differ by seconds — see that constant for the false RED a zero-tolerance rule produced.
  const age = now - seen;
  return age >= -ENV_FUTURE_SKEW_MS && age <= ENV_STALE_AFTER_MS;
}


export function envStateIsUnknown(env) {
  return !ENV_KNOWN_STATES.has(String(env?.status || "").trim().toLowerCase());
}

export function describeEnv(env) {
  const seen = String(env?.lastSeen || "").trim();
  const state = String(env?.status || "unknown").trim().toLowerCase() || "unknown";
  return `${env?.id || "(unnamed)"} [${state}${seen ? `, last seen ${seen}` : ""}]`;
}

// ── `bridge-installed` staleness ─────────────────────────────────────────────────────
//
// N13 (bug-hunt 2026-07-31). The check compared the installed marker sha to repo HEAD and failed on
// ANY difference:
//
//     if (sha === repo.sha) return ok;
//     return stale(`installed copy is X, repo HEAD is Y (N commit(s) behind)`);
//
// So a docs-only commit — or a service-only one — reported "the bridge is stale, re-run install.sh",
// which is false: nothing under `mcp/stdio/` changed, and the running bridge is executing exactly
// the code the checkout describes. Observed live: three consecutive roadmap commits and one
// service+dashboard commit each produced the warning.
//
// That is not a cosmetic annoyance. `bridge-installed` is one of the few checks that catches a REAL
// and completely silent failure (you edited the bridge, forgot install.sh, and the checkout lies to
// you). A check that fires on commits it has no opinion about trains the operator to skim past it —
// and then the one time it means something, it reads the same as the twenty times it did not. Alarm
// fatigue is how a true positive becomes invisible, which is the same outcome as a false green by a
// different route.
//
// AND IT ANSWERED A QUESTION IT CANNOT SEE, until 2026-08-28. The stale branch said "The RUNNING
// bridge is older than the checkout" -- a claim about a PROCESS, from a function whose only inputs
// are two shas and two commit counts. It cost the operator restarts of the wrong component; the
// incident and the gate that now forbids the shape are in
// tests/a-check-describes-only-what-it-measured.test.js.
//// The honest question is not "is the marker equal to HEAD?" but "have any commits since the marker
// TOUCHED the bridge?". Kept pure — the caller does the `git log -- mcp/stdio` and passes counts in.
export function bridgeInstallVerdict({ installedSha = "", headSha = "", headShort = "", bridgeCommits = 0, totalCommits = 0 } = {}) {
  const short = String(installedSha || "").slice(0, 7);
  if (!installedSha) {
    return { ok: false, code: "unknown-version", detail: "Bridge present but has no version marker.",
      fix: "Re-run install.sh to stamp it." };
  }
  if (!headSha) {
    return { ok: true, code: "ok", detail: `installed — ${short} (no checkout to compare against)`, fix: "" };
  }
  if (installedSha === headSha) {
    return { ok: true, code: "ok", detail: `installed — ${short} == repo HEAD`, fix: "" };
  }
  if (Number(bridgeCommits) > 0) {
    const n = Number(bridgeCommits);
    return {
      ok: false,
      code: "stale",
      detail: `installed copy is ${short}, repo HEAD is ${headShort} — ${n} commit(s) since then changed `
        + `mcp/stdio/. That is the code ON DISK; what any bridge is RUNNING is a separate `
        + `question that bridge-current answers.`,
      fix: "Re-run `bash install.sh --client <runtime>` to put the new code on disk. A running "
        + "bridge keeps what it loaded at boot, so bridge-current is what asks for the relaunch "
        + "and names which ones.",
    };
  }
  // Behind, but by commits that cannot affect the bridge. Say so rather than crying wolf.
  return {
    ok: true,
    code: "ok",
    detail: `installed — ${short}; repo HEAD is ${headShort} (${Number(totalCommits) || 0} commit(s) ahead, `
      + `none touching mcp/stdio/)`,
    fix: "",
  };
}

// ── `service` staleness — the SAME lesson as bridgeInstallVerdict, one check over ─────
//
// N13 taught this for the bridge and the fix was never carried across to the service check,
// which kept doing naive `built_sha === repo HEAD` and, on any difference, announced:
//
//     "serving build X, repo HEAD is Y (N commit(s) behind).
//      Your service changes are NOT running. Rebuild."
//
// Observed live 2026-08-10 after three commits that touched only `docs/` and `scripts/`.
// There were no service changes; the sentence was simply false, and `--strict` exited 1 on
// it, so any script or CI gate would have failed on a documentation commit.
//
// FOURTH false verdict in this tool — after the counted-registrations false green
// (`756f3a5`), the container/host clock-skew false red (ENV_FUTURE_SKEW_MS) and the OpenAI
// token false red (openAiUsageVerdict). Same cost every time: a check that goes red on a
// benign condition trains the operator to skim past it, and then the one time it means
// something it reads like the twenty times it did not.
//
// The honest question is not "is the running sha equal to HEAD?" but "have any commits since
// the build touched something the IMAGE actually contains?". Kept pure — the caller runs the
// path-scoped `git log` and passes counts in, exactly as `bridgeInstallVerdict` does.
//
// RUNTIME content, not image bytes — the distinction the reviewer insisted on, and it matters.
// The first cut mirrored the Dockerfile's COPY lines and called that "image content". Honest about
// bytes, wrong for a verdict an operator ACTS on: the Dockerfile copies `mcp/` whole, so a
// host-side bridge edit demanded a container rebuild for code the container never executes. That
// is a smaller wolf-cry of the same species as the one being fixed, and it fired immediately on
// the very commit that shipped the fix.
//
// So the question is "did anything the RUNNING SERVICE EXECUTES change?". Evidence for this set,
// checked rather than assumed (and independently re-checked by the reviewer): the container's CMD
// is `uvicorn service.main:app`; `service/main.py` dynamically loads only `/app/mcp/sse_server.py`
// for the SSE transport; an AST scan of non-test `service/**` plus `mcp/sse_server.py` finds no
// import of `mcp.stdio` (the `from mcp.server.fastmcp` hits are the PyPI package); and CLAUDE.md
// documents exactly this split.
export const SERVICE_RUNTIME_PATHS = [
  "service", "mcp", "config", "Dockerfile",
];

// Third instance of the same class, found by the fix flagging its OWN commit: `service/` is a
// runtime path, so adding `service/tests/test_service_runtime_boundary.py` demanded a container
// rebuild. Nothing in the image runs pytest — the CMD is `uvicorn service.main:app` and the
// Dockerfile installs no test runner — so a test-only commit cannot change what the service
// executes. Excluded as git pathspecs by the caller; verified against the real repo (1 commit with
// tests included, 0 with them excluded).
//
// Kept as an EXCLUDE list rather than by narrowing SERVICE_RUNTIME_PATHS to specific
// subdirectories, because the safe default for a new directory under `service/` is "this is
// runtime, demand a rebuild". Opt-out beats opt-in when the wrong answer is a false green.
//
// `mcp` FOLLOWS THE SAME RULE AS OF 2026-08-15, and did not until then. It was listed as the exact
// file `mcp/sse_server.py`, which is opt-IN: a second runtime module beside it — the obvious result
// of decomposing a 730-line file — would have been cargo by default, and doctor would have reported
// the container clean while the code it runs had changed. That is the same false green this list's
// own comment argues against one paragraph up; the rule was simply never carried across to `mcp`.
// Nothing detects the difference until the day someone adds the file, and then nothing detects it
// at all, which is why this is a default rather than a check.
//
// The flip is behaviour-neutral today, measured rather than assumed: over the last 200 commits both
// pathspecs select the same 136 runtime commits, and `mcp/` currently holds nothing but
// `sse_server.py` and `stdio/`.
export const SERVICE_RUNTIME_EXCLUDE_PATHS = [
  "service/tests",
  "service/**/*.test.mjs",
  // Host-side bridge code the container never executes. Frequently edited, so leaving it in would
  // demand a rebuild on most bridge commits — noise that trains the operator to ignore the check.
  "mcp/stdio",
];

// Copied into the image but NOT executed by the service. Kept as an explicit, reasoned allowlist
// rather than simply omitted, so `doctor-service-staleness.test.js` can still assert that every
// Dockerfile COPY source is accounted for SOMEWHERE. Silent omission would let a new COPY of
// genuinely-runtime code slip in with doctor reporting clean — a false green, the worse direction.
// FOURTH INSTANCE OF THE CLASS ABOVE, and the reasoning was already written one paragraph up.
//
// `bridge-installed` counts commits since the installed marker that touched `mcp/stdio`, and it
// counted test-only commits. install.sh copies the whole directory into ~/.aify-comms -- 349 test
// files at the time of writing -- and NOTHING ever executes them from there; the suites run from
// the checkout. So a commit under mcp/stdio/tests cannot change a single byte the bridge runs.
//
// What made it worth fixing rather than noting is the REMEDY. bridge-installed's fix line is
// `re-run install.sh AND relaunch the wrappers`, and relaunching the environment bridge reaps its
// managed workers. So a test-only commit could ask the operator to kill the running fleet for a
// change with no runtime effect -- a false alarm whose cost is measured in lost sessions, from a
// check whose own documentation says it must not cry wolf on every commit.
//
// Measured on the real repo: 4 commits since the installed sha touched mcp/stdio, 3 with runtime
// files and 1 (e19ae974) touching only mcp/stdio/tests. With this exclusion the count is 3.
//
// An EXCLUDE list for the same reason the service one is: the safe default for a new directory
// under mcp/stdio is 'this is runtime, demand a reinstall'. Opt-out beats opt-in when the wrong
// answer is a false green.
export const BRIDGE_RUNTIME_EXCLUDE_PATHS = [
  "mcp/stdio/tests",
];

export const SERVICE_IMAGE_NON_RUNTIME_PATHS = {
  "mcp": "partially runtime: mcp/ is listed in SERVICE_RUNTIME_PATHS, so anything added beside "
    + "sse_server.py is runtime by default; mcp/stdio is excluded there as host-side bridge code "
    + "the container never executes (covered by the bridge-installed check)",
  "integrations": "installer/host integration flows; no runtime consumer in service.main or "
    + "mcp/sse_server.py",
  ".agents": "Codex skill mirror cargo; referenced only by health/info text and tests",
  "install.sh": "operator installer shipped for convenience; never executed by the service",
};

/**
 * The `/version` payload turned into a verdict. The ONLY path from that response to a `service` check.
 *
 * IT EXISTS BECAUSE THE CALL SITE HAD A HOLE THE PREDICATE COULD NOT SEE. `checkService()` returned
 * early when no git checkout was found -- `ok: true`, displaying `ver.sha_short` straight from the
 * payload -- so `serviceBuildVerdict` was never called at all. On any host without a checkout, which is
 * the ordinary deployed case, an env-supplied build identity was reported HEALTHY and the supplied
 * short was shown as the running build. The predicate's own no-checkout branch was unreachable from
 * the consumer, and so was every override rule it had just been taught.
 *
 * That is the helper-versus-call-site shape again, and the reason the mapping is now a named function
 * a test can execute rather than four lines inside an async routine that needs a network and a repo.
 */
export function serviceVerdictFrom(version, { headSha = "", headShort = "", runtimeCommits = 0, totalCommits = 0 } = {}) {
  return serviceBuildVerdict({
    builtSha: String(version?.sha || ""),
    builtShort: version?.sha_short || "",
    headSha,
    headShort,
    runtimeCommits,
    totalCommits,
    identityOverriddenBy: Array.isArray(version?.identityOverriddenBy) ? version.identityOverriddenBy : [],
  });
}

/**
 * The build verdict, with any non-SHA provenance caveat appended to WHATEVER it decides.
 *
 * TWO BRANCHES USED TO CARRY IT and four did not, so a stale or unknown build silently dropped the
 * fact that its version or branch came from the environment -- while the contract said the caveat
 * accompanied the verdict. Appending at the boundary rather than inside each branch is what makes that
 * true for the branches nobody thought about, including ones added later.
 */
export function serviceBuildVerdict(input = {}) {
  const overridden = Array.isArray(input.identityOverriddenBy)
    ? input.identityOverriddenBy.filter(Boolean)
    : [];
  const verdict = buildVerdict(input);
  const caveat = overridden.filter((f) => f !== "build_sha" && f !== "build_short");
  if (!caveat.length || verdict.code === "build-identity-overridden") return verdict;
  return { ...verdict, detail: `${verdict.detail} (${caveat.join(", ")} came from the environment)` };
}

function buildVerdict({
  builtSha = "", headSha = "", headShort = "", builtShort = "",
  runtimeCommits = 0, totalCommits = 0, identityOverriddenBy = [],
} = {}) {
  // ONLY `build_sha` INVALIDATES THE COMPARISON, and refusing on all five was over-broad enough to
  // destroy evidence. This check compares `build_sha` against repo HEAD, so:
  //
  //   * `build_sha` env-supplied  -> the comparison can be MANUFACTURED. Refuse to certify.
  //   * `build_short` env-supplied -> display only; the short is derived from the trusted full sha
  //     below rather than taken from the payload, so an overridden short cannot become evidence.
  //   * version / branch / built_at -> do not participate in the predicate at all. A caveat, not a
  //     reason to withhold a sha comparison that is still exactly valid -- and `SERVICE_VERSION` is a
  //     DOCUMENTED override, so refusing on it punished the one case the design allows for.
  const overridden = Array.isArray(identityOverriddenBy) ? identityOverriddenBy.filter(Boolean) : [];
  const shaSupplied = overridden.includes("build_sha");
  // Derived, never taken from the payload: `builtShort` is one of the fields env can supply.
  const short = (overridden.includes("build_short") || !builtShort)
    ? String(builtSha || "").slice(0, 7)
    : builtShort;
  if (shaSupplied) {
    return {
      ok: false,
      code: "build-identity-overridden",
      detail: "healthy, but its build SHA came from the environment, so comparing it against a "
        + "checkout proves nothing about what is running.",
      fix: "Unset AIFY_BUILD_SHA and let scripts/stamp.sh supply it, so this check measures the "
        + "running build instead of a value someone supplied.",
    };
  }
  if (!builtSha) {
    return { ok: false, code: "unknown-build", detail: "healthy, but it reports no build sha.",
      fix: "Run `scripts/stamp.sh` before `docker compose up -d --build` — otherwise /version lies." };
  }
  if (!headSha) {
    return { ok: true, code: "ok", detail: `healthy — build ${short} (no checkout to compare against)`, fix: "" };
  }
  if (builtSha === headSha) {
    return { ok: true, code: "ok", detail: `healthy — build ${short} == repo HEAD`, fix: "" };
  }
  if (Number(runtimeCommits) > 0) {
    const n = Number(runtimeCommits);
    return {
      ok: false,
      code: "stale",
      detail: `serving build ${short}, repo HEAD is ${headShort} — ${n} commit(s) since then changed `
        + `code the service EXECUTES. The RUNNING service is older than the checkout.`,
      fix: "Rebuild: `bash scripts/stamp.sh && docker compose up -d --build`.",
    };
  }
  // A DIFFERENT sha that is ZERO commits behind is a contradiction, not a clean bill of health.
  //
  // Found by an external review of the release ladder, 2026-08-11, and it is the same class this
  // whole tool spent the day on. `doctor.js` computes both counts with
  // `git rev-list --count <builtSha>..HEAD`. If the built sha is not in local history — deployed
  // from an unmerged branch, a force-push, a divergent checkout — that git call FAILS, `sh()`
  // returns "", both counts become 0, and control fell through to the branch below reporting
  // "healthy — 0 commit(s) ahead". A false green for precisely the "serving ≠ HEAD" case the check
  // exists to catch, in the instrument whose entire theme is not passing on absent evidence.
  //
  // Reproduced: serviceBuildVerdict({builtSha:'deadbeef…', headSha:'cafebabe…', runtimeCommits:0,
  // totalCommits:0}) returned ok:true.
  if (Number(totalCommits) === 0) {
    return {
      ok: false,
      code: "unknown-build",
      detail: `serving build ${short} but repo HEAD is ${headShort}, and git reports ZERO commits `
        + "between them — which cannot both be true. The built sha is probably not in this "
        + "checkout's history (deployed from an unmerged branch, a force-push, or a different "
        + "clone), so nothing here can tell you whether the running service is current.",
      fix: "Fetch the branch the container was built from, or rebuild from this checkout: "
        + "`bash scripts/stamp.sh && docker compose up -d --build`.",
    };
  }
  // Behind, but by commits that cannot reach the container. Say so instead of crying wolf.
  return {
    ok: true,
    code: "ok",
    detail: `healthy — build ${short}; repo HEAD is ${headShort} (${Number(totalCommits) || 0} commit(s) ahead, `
      + `none touching code the service executes)`,
    fix: "",
  };
}

// ── `bridge-current`: is the RUNNING bridge executing current code? ──────────────────
//
// v0.2 item B1, promoted after it cost a real hour. `bridge-installed` proves the FILES on disk
// match the checkout; it says nothing about what any live PROCESS loaded at boot. `bridge-running`
// was supposed to close that, but it reads /proc and SKIPS on Windows — so on this host nothing
// verified that a running wrapper executes current code.
//
// THE LIVE ARTIFACT: on 2026-08-10 I verified a just-shipped multipart fix through comms_share,
// saw the OLD corrupted bytes, and nearly recorded a working fix as broken. The fix was correct;
// my own bridge was pre-restart, and no check said so.
//
// The bridge already computed its build sha for the startup banner and then wrote it only to
// stderr. Reporting it on registration makes this platform-independent: compare what each LIVE
// bridge says it is running against the checkout, with no process inspection at all.
//
// Deliberately reports a RESTART, never a reinstall — the files are already current in this case,
// so `install.sh` would change nothing and telling the operator to run it would be a wrong fix.
// `bridgeCommitsSince` maps a reported build sha -> how many commits between it and HEAD actually
// TOUCHED `mcp/stdio`. Without it this check called a bridge stale for a docs-only commit and told
// the operator to restart it, which is the same cry-wolf `bridge-installed` already fixed for the
// files on disk (N13): being behind is not the question, running different CODE is. A build with
// no entry is treated as stale, not as clean — an unanswerable delta is not evidence of currency.
export function bridgeCurrentVerdict({ environments = [], headSha = "", headShort = "", bridgeCommitsSince = {} } = {}) {
  const head = String(headSha || "");
  if (!head) {
    return { ok: true, code: "skipped", detail: "no checkout to compare running bridges against", fix: "" };
  }
  // Only rows where a BRIDGE is live -- `envCanClaimASpawn`, not `envIsOnline`. A dead bridge's
  // build is not a claim about anything running, and since aify-env began advertising, `status:
  // online` no longer means a bridge is there at all. Measured 2026-09-02: this reported "1 live
  // bridge is RUNNING older code" against a host whose last bridge had spoken 26 hours earlier --
  // naming a stale build for a process that did not exist, and sending the operator to relaunch a
  // wrapper rather than to start a bridge.
  const live = environments.filter((e) => envCanClaimASpawn(e));
  if (!live.length) {
    return { ok: true, code: "skipped", detail: "no live environment bridge to check", fix: "" };
  }
  const stale = [];
  const behindByNonBridge = [];
  let unknown = 0;
  for (const env of live) {
    const build = String(env?.metadata?.bridgeBuild || "").trim();
    if (!build || build === "unknown" || build === "no-git" || build === "unknown-ref") {
      unknown += 1;
      continue;
    }
    // The bridge reports a 12-char prefix; compare on the shorter of the two.
    const n = Math.min(build.length, head.length);
    if (build.slice(0, n) === head.slice(0, n)) continue;
    const touched = bridgeCommitsSince?.[build];
    if (touched === 0) {
      // Behind HEAD, but not one of those commits changed a byte the bridge executes.
      behindByNonBridge.push(`${env?.id || "(unnamed)"} at ${build.slice(0, 7)}`);
      continue;
    }
    stale.push(`${env?.id || "(unnamed)"} running ${build.slice(0, 7)}`);
  }
  if (stale.length) {
    return {
      ok: false,
      code: "stale-process",
      detail: `${stale.length} live bridge(s) are RUNNING older code than the checkout `
        + `(repo HEAD ${headShort}): ${stale.join(", ")}. The files on disk may already be current — `
        + "a process keeps what it loaded at boot.",
      fix: "RESTART those bridges/wrappers. Re-running install.sh will not help if bridge-installed "
        + "is already green — the code is on disk, just not in memory.",
    };
  }
  // AUDIT 4/4 F1, live-confirmed on this host: the ONE online environment bridge reported no
  // `bridgeBuild` at all, so this check returned ok and `aify-doctor --strict` PASSED — while
  // proving nothing whatsoever about what the running bridge executes. That is the same false
  // green as `env-bridge` counting registered rows (756f3a5), in the check written to prevent it.
  //
  // So the two absences must not share a verdict. Some-current-some-silent is degraded reporting
  // on a partially proven fleet. ZERO current evidence is not a partial result, it is NO result,
  // and a check with no result must not be counted as a passed one.
  if (unknown === live.length) {
    return {
      ok: false,
      code: "unknown-all",
      detail: `none of the ${live.length} live bridge(s) report which build they are running, so `
        + `nothing here verifies them against repo HEAD ${headShort}. This is NOT "they are current" `
        + "— it is no evidence either way (every bridge predates the build-stamp report, or none "
        + "has restarted since).",
      fix: "Restart the bridges/wrappers. They report their build on registration from then on, "
        + "and this check becomes real. Re-running install.sh alone will not do it — a process "
        + "keeps what it loaded at boot.",
    };
  }
  if (unknown) {
    return {
      ok: true,
      code: "partial",
      detail: `${live.length - unknown} live bridge(s) match repo HEAD; ${unknown} did not report a `
        + "build sha (pre-B1 bridge — restart it to start reporting)",
      fix: "",
    };
  }
  if (behindByNonBridge.length) {
    return {
      ok: true,
      code: "ok-nonbridge",
      detail: `${live.length} live bridge(s) are running current BRIDGE code; ${behindByNonBridge.length} `
        + `report an older sha than repo HEAD ${headShort} (${behindByNonBridge.join(", ")}) but no commit `
        + "in between touched `mcp/stdio`, so there is nothing for a restart to pick up.",
      fix: "",
    };
  }
  return { ok: true, code: "ok", detail: `${live.length} live bridge(s) running repo HEAD ${headShort}`, fix: "" };
}

// ── `skills-installed` ───────────────────────────────────────────────────────────────
//
// Skills are a DEPLOY PATH, and until 2026-08-03 they had no verifier. install.sh copies
// .claude/skills/* into ~/.claude/skills/ and .agents/skills/* into the hermes tree, so editing the
// checkout changes NOTHING for a running fleet — the identical silent-staleness failure that
// bridge-installed exists to catch, on guidance that steers every agent's behaviour.
//
// This check compares CONTENT rather than a marker sha, which is strictly stronger than the bridge
// check: it also catches a copy someone edited in place, and it cannot be fooled by a marker that
// was stamped without the files actually landing.
//
// It deliberately does NOT prove the fleet is running the new text. A skill is read at session
// start, so a RUNNING agent keeps whatever it loaded — the fix line says so, because "install.sh
// succeeded" has been mistaken for "the change is live" in this repo before.
// WHERE THE SKILL TREES ARE INSTALLED, resolved rather than assumed.
//
// Lives here, not in doctor.js, because doctor.js RUNS ITS CHECKS ON IMPORT -- importing it to test
// one function executes the whole doctor, network calls included. Found the hard way. The repo's own
// rule already says it: pure helpers move out of the bridges so their logic can fail a test instead
// of only failing in production.
//
// Every dependency is injected -- home, env, and the existence probe -- so a test can describe a host
// that has Codex but not hermes without creating directories.
//
// A destination is returned only when its runtime HOME exists: no CODEX_HOME means Codex is not
// installed here and nothing can be stale. If the home exists and the skills under it do not, the
// caller reports them missing. Absence of the RUNTIME is a skip; absence of the SKILLS is a finding.
export function skillDestinations({ home = homedir(), env = process.env, exists = existsSync } = {}) {
  const out = [
    { src: [".claude", "skills"], dst: join(home, ".claude", "skills"), label: "~/.claude/skills" },
  ];
  const codexHome = env.CODEX_HOME || join(home, ".codex");
  if (exists(codexHome)) {
    out.push({ src: [".agents", "skills"], dst: join(codexHome, "skills"), label: `${codexHome}/skills` });
  }
  const hermesHome = env.HERMES_HOME || join(home, ".hermes");
  if (exists(hermesHome)) {
    out.push({
      src: [".agents", "skills"],
      dst: join(hermesHome, "skills", "autonomous-ai-agents"),
      label: `${hermesHome}/skills/autonomous-ai-agents`,
    });
  }
  return out;
}

export function skillsInstallVerdict({ missing = [], differing = [], total = 0, dest = "" } = {}) {
  const miss = Array.isArray(missing) ? missing : [];
  const diff = Array.isArray(differing) ? differing : [];
  const where = dest ? ` (${dest})` : "";
  if (!total) {
    return {
      ok: false,
      code: "not-installed",
      detail: `No installed skills found${where}.`,
      fix: "Run `bash install.sh --client <claude|codex|hermes>` to install the skill trees.",
    };
  }
  if (!miss.length && !diff.length) {
    return { ok: true, code: "ok", detail: `installed skills match the checkout (${total} file(s))${where}`, fix: "" };
  }
  const parts = [];
  if (diff.length) parts.push(`${diff.length} differ: ${diff.slice(0, 4).join(", ")}${diff.length > 4 ? " …" : ""}`);
  if (miss.length) parts.push(`${miss.length} missing: ${miss.slice(0, 4).join(", ")}${miss.length > 4 ? " …" : ""}`);
  return {
    ok: false,
    code: "stale",
    detail: `installed skills do NOT match the checkout${where} — ${parts.join("; ")}`,
    fix: "Re-run `bash install.sh --client <runtime>`, THEN restart the agents — a skill is read at "
      + "session start, so a running agent keeps the text it already loaded.",
  };
}

// ── process inspection: the two readers behind the `agent-identity` check ─────────────────────────
//
// MOVED HERE from doctor.js in v0.5.4, byte-identical apart from an injectable `/proc` root. Same
// reason the env-bridge predicates came first: doctor.js runs every check at import and ends in
// `process.exit()`, so nothing declared there can be reached by a test. A V8-coverage census of the
// bridge suite found both with a ZERO call count.
//
// WHAT THEY DECIDE. `agent-identity` is the check that catches an agent which registered but whose
// PROCESS has no `AIFY_AGENT_ID` — invisible from the database, because it messages and heartbeats
// perfectly while its status latches forever. The check has to separate that from a plain
// claude+comms session that never registered and is legitimately id-less, and `readBoundAgentId` IS
// that separation: `comms_register` writes a binding file keyed by the CLIENT pid, so a binding
// means "this session registered". Get it wrong in one direction and the check cries wolf on every
// plain session; in the other it reports green over exactly the agents it exists to find.
//
// THE ROOT IS A PARAMETER so a test can stage a fake /proc tree. It defaults to "/proc" and the
// callers pass nothing, so behaviour on a real host is unchanged — and the check is Linux-gated
// anyway.

// The agent binding comms_register writes, keyed by the CLAUDE pid (the bridge's parent).
export function readBoundAgentId(bridgePid, { procRoot = "/proc", readFile = readFileSync, tmpDir = null } = {}) {
  let ppid = "";
  try { ppid = (readFile(`${procRoot}/${bridgePid}/stat`, "utf8").split(" ")[3] || "").trim(); } catch { return ""; }
  const tmp = tmpDir || process.env.TMPDIR || process.env.TEMP || "/tmp";
  for (const pid of [ppid, String(bridgePid)]) {
    if (!pid) continue;
    try {
      const raw = readFile(join(tmp, `aify-agent-${pid}`), "utf8").trim();
      if (!raw) continue;
      const id = raw.startsWith("{") ? String(JSON.parse(raw).agentId || "") : raw;
      if (id) return id;
    } catch { /* no binding for this pid */ }
  }
  return "";
}

export function readProcEnv(pid, { procRoot = "/proc", readFile = readFileSync } = {}) {
  const out = {};
  try {
    for (const kv of readFile(`${procRoot}/${pid}/environ`, "utf8").split("\0")) {
      const i = kv.indexOf("=");
      if (i > 0) out[kv.slice(0, i)] = kv.slice(i + 1);
    }
  } catch { /* process gone / not ours */ }
  return out;
}

/**
 * Where managed spawns run, and whether that place is answering.
 *
 * Delegation makes aify-env REQUIRED for spawning: `startDelegated` refuses rather than falling back,
 * because a silent fallback would put two spawners on one host, which is the collision the environment
 * tier exists to end. Refusing is right and invisible -- an operator sees spawns failing, not a daemon
 * that is down -- so this check exists to name the cause before it is needed.
 *
 * READ FROM THE LAUNCHER FILE, never by running it: a bare `aify-comms` starts an environment bridge
 * and supersedes the live one, which is how this fleet lost nine managed agents in August.
 *
 * @param {{launcherText: string|null, endpointAnswered: boolean|null}} input
 *   launcherText: the installed `aify-comms` launcher, or null if it could not be read.
 *   endpointAnswered: whether aify-env replied; null when it was not asked (delegation off).
 */
/**
 * Read the delegation settings out of an installed `aify-comms` launcher. THE ONE PARSER.
 *
 * This question had THREE implementations: this module, `doctor.js` (which re-ran both regexes to
 * decide whether to probe the endpoint), and `scripts/installed-delegation.sh`. The comment further
 * down this file says why that is a defect -- "a second implementation of one question does not agree
 * for free, it agrees until one of them is fixed" -- and it was written about four checks that left
 * this tool for exactly that reason.
 *
 * The asymmetry is what makes it worth fixing rather than noting. If the launcher's shape drifts and
 * only `doctor.js` is updated, it probes aify-env, gets a real answer, and hands it to a verdict whose
 * own stale regex reports `pre-contract` -- ok: TRUE. A false green, built from a probe it paid for
 * and discarded.
 *
 * THE SHEBANG CHECK IS A CONTROL ON THE INSTRUMENT, not a style rule. `doctor.js` reads
 * `~/.local/bin/aify-comms` and falls back to `aify-comms.cmd`, which on Windows is a six-line shim
 * that execs the bash file. It carries no settings at all, so parsing it yields "no delegation line
 * found" -- indistinguishable from a genuinely old launcher, and reported as `pre-contract`, ok:true.
 * A file that is not the launcher body cannot testify about the launcher: that is `unknown-all`.
 */
export function launcherDelegation(launcherText) {
  const text = typeof launcherText === "string" ? launcherText : "";
  // A rendered launcher is a bash script. The .cmd shim starts `@echo off` and a pre-contract
  // launcher still starts `#!`, so this separates "wrong file" from "old file" without reclassifying
  // any real launcher.
  const isLauncher = /^#!/.test(text);
  const setting = /^export AIFY_COMMS_DELEGATE_SPAWNS="([^"]*)"/m.exec(text);
  return {
    isLauncher,
    present: Boolean(setting),
    // The DECIDER's rule, not a second one. Reporting "delegated" for a value the spawn path
    // treats as off is how `spawn-delegation` came to contradict where spawns actually run.
    on: Boolean(setting) && delegationOptedIn(setting[1]),
    endpoint: (/^export AIFY_ENV_ENDPOINT="([^"]*)"/m.exec(text) ?? [, ""])[1],
  };
}


export function spawnDelegationVerdict({ launcherText = null, endpointAnswered = null } = {}) {
  if (launcherText === null) {
    return {
      ok: false,
      code: "unknown-all",
      detail: "Could not read the installed aify-comms launcher, so where spawns run is unknown. "
        + "Nothing was verified.",
      fix: "Run install.sh for any client; it writes the environment-bridge launcher.",
    };
  }
  const parsed = launcherDelegation(launcherText);
  if (!parsed.isLauncher) {
    return {
      ok: false,
      code: "unknown-all",
      detail: "The file read in place of the aify-comms launcher is not a launcher body (no shebang), "
        + "so it cannot say where spawns run. On Windows this is the .cmd shim, which carries no "
        + "settings. Nothing was verified.",
      fix: "Check ~/.local/bin/aify-comms is readable, then re-run.",
    };
  }
  if (!parsed.present) {
    // A launcher rendered before the setting existed. Not a failure: it hosts spawns itself, which is
    // the behaviour every host had before v0.6 and still the default.
    return {
      ok: true,
      code: "pre-contract",
      detail: "This launcher predates the delegation setting, so the bridge hosts managed spawns "
        + "itself — the default. Reinstall to make the setting explicit in the file.",
      fix: "",
    };
  }
  if (!parsed.on) {
    return {
      ok: true,
      code: "local",
      detail: "Managed spawns are hosted by the aify-comms bridge itself. aify-env is not in the "
        + "spawn path, so its process list is empty by design.",
      fix: "",
    };
  }
  const endpoint = parsed.endpoint;
  if (endpointAnswered === true) {
    return {
      ok: true,
      code: "delegated",
      detail: `Managed spawns are delegated to aify-env at ${endpoint || "(no endpoint baked)"}, `
        + "which is answering.",
      fix: "",
    };
  }
  if (endpointAnswered === null) {
    return {
      ok: false,
      code: "unknown-all",
      detail: `Delegation is on, pointing at ${endpoint || "(no endpoint baked)"}, and it was not `
        + "asked whether that is answering. Nothing was verified.",
      fix: "Re-run the doctor with network access to the endpoint.",
    };
  }
  return {
    ok: false,
    code: "unreachable",
    detail: `Delegation is on but aify-env at ${endpoint || "(no endpoint baked)"} is not answering. `
      + "Every managed spawn will FAIL until it is: the bridge refuses rather than silently hosting "
      + "them itself, because two spawners on one host is the collision this tier exists to end.",
    // `--no-delegate-spawns`, NOT "without --delegate-spawns". Omitting the flag CARRIES THE
    // SETTING FORWARD -- install.sh reads the installed launcher and prints "keeping DELEGATED to
    // aify-env at <endpoint> (installed setting)" -- which is deliberate, so that a reinstall for
    // an unrelated reason never moves a host's spawns. This text said the opposite, in front of an
    // operator whose every managed spawn is failing.
    fix: "Start aify-env on this host, or reinstall with `--no-delegate-spawns` to host spawns "
      + "locally. Omitting the flag does NOT turn delegation off: install.sh carries the installed "
      + "setting forward.",
  };
}
/**
 * Managed delivery loops running for agents that no longer belong to the live environment bridge.
 *
 * WHY THIS IS A CHECK AND NOT A REAPER. An orphaned loop is a `hermes-managed-host.js run <agent>`
 * process, launched detached under nohup so it deliberately OUTLIVES whatever started it. Nothing
 * collects one during normal operation: the survivor sweep runs at bridge BOOT, so a loop orphaned
 * mid-session accumulates until the next relaunch. Six were alive on the operator's host on
 * 2026-08-26, the oldest at 96 minutes, each holding a hermes gateway.
 *
 * They are also INVISIBLE from the control plane, which is the reason this belongs in doctor rather
 * than the dashboard: the agent reads `available` because it has no live channel sidecar, and its
 * `lastSeen` keeps refreshing because the orphan itself is heartbeating. The liveness signal that
 * would prove it dead is the one the orphan keeps emitting. An operator reading the dashboard sees
 * "not running" beside a process that is running -- the operator asked exactly this on 2026-08-26,
 * having watched agents "seem to be running still" after the panel reported them dead.
 *
 * IT REPORTS AND NEVER KILLS. Deciding to kill needs ownership this predicate does not have, and the
 * repo already has one env-scoped reaper that does it correctly at the only moment it is safe.
 *
 * @param {object}   input
 * @param {Array|null} input.loops  [{agentId, pid}] enumerated loops, or null if unreadable.
 * @param {object|null} input.agents  {agentId: {sessionMode, runtimeState}}, or null if unreachable.
 * @param {string|null} input.liveBridgeId  The ONLINE environment's bridgeId, or null if unknown.
 */
export function managedOrphanVerdict({ loops = null, agents = null, liveBridgeId = null } = {}) {
  // NO EVIDENCE IS NOT A PASS. Each of the three inputs can be absent for its own reason -- an
  // unreadable process table, a service that is down, no bridge online -- and every one of them makes
  // the answer unknown rather than clean. `env-bridge` and `bridge-current` both shipped as
  // green-by-default and both were wrong the same way (`a2f9e42`, `756f3a5`).
  const missing = [];
  if (loops === null) missing.push("the process table could not be read");
  if (agents === null) missing.push("the service did not answer");
  if (!liveBridgeId) missing.push("no environment bridge is online");
  if (missing.length) {
    return {
      ok: false,
      code: "unknown-all",
      detail: `Orphaned managed workers could not be counted: ${missing.join("; ")}. Nothing was `
        + "verified, so this is not a clean result.",
      fix: "Fix the named condition, then re-run. `aify-comms doctor` reports each of them separately.",
    };
  }

  // ONE LOGICAL LOOP PER AGENT. Enumeration matches on the command line, and the launcher is
  // `nohup node hermes-managed-host.js run <agent>` -- so the nohup parent AND its node child both
  // match, and counting pids would report every loop twice.
  const byAgent = new Map();
  for (const loop of loops) {
    const agentId = String(loop?.agentId || "").trim();
    if (!agentId) continue;
    if (!byAgent.has(agentId)) byAgent.set(agentId, []);
    byAgent.get(agentId).push(loop.pid);
  }
  if (byAgent.size === 0) {
    return { ok: true, code: "none", detail: "No managed delivery loops are running on this host.", fix: "" };
  }

  const orphans = [];
  for (const [agentId, pids] of byAgent) {
    const row = agents[agentId];
    // A loop for an agent the service does not know is orphaned by definition: nothing can address it.
    const boundTo = String(row?.runtimeState?.bridgeInstanceId || "").trim();
    if (boundTo && boundTo === liveBridgeId) continue;
    orphans.push({ agentId, pids: pids.slice().sort((a, b) => a - b), boundTo: boundTo || "(none)" });
  }
  if (orphans.length === 0) {
    return {
      ok: true,
      code: "ok",
      detail: `${byAgent.size} managed delivery loop(s) running, all bound to the live environment `
        + "bridge.",
      fix: "",
    };
  }
  const named = orphans
    .map((o) => `${o.agentId} (pid ${o.pids.join("+")}, bound to ${o.boundTo})`)
    .join(", ");
  return {
    ok: false,
    code: "orphaned",
    detail: `${orphans.length} of ${byAgent.size} managed delivery loop(s) belong to no live bridge, `
      + `so they hold a gateway and a session that nothing can address: ${named}. Their agents read `
      + "`available` on the dashboard while these processes are running.",
    fix: "Restart each named agent -- the wrapper reaps that agent's prior loop as it spawns -- or "
      + "relaunch the environment bridge, whose boot survivor sweep collects them all.",
  };
}
