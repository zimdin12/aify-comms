// Pure predicates behind aify-doctor's `env-bridge` check.
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
// The honest question is not "is the marker equal to HEAD?" but "have any commits since the marker
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
        + `mcp/stdio/. The RUNNING bridge is older than the checkout.`,
      fix: "Re-run `bash install.sh --client <runtime>` AND relaunch the wrappers — bridge edits do "
        + "NOT take effect from the checkout, and a relaunch is what puts the new code in memory.",
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
