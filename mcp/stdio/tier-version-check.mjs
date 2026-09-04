// Is the aify-env SERVING this host new enough for the aify-comms installed on it?
//
// EXTERNAL REVIEW, Round 8, preamble: "aify-env/aify-wrapper both still say 0.6.0 with no tags --
// nothing gates cross-tier version agreement." Three repos ship one product and no instrument
// compared them, in a checkout or on a live host.
//
// THE LIVE QUESTION IS THE ONE THAT MATTERS, and it is answerable: aify-env sends `bridgeVersion` on
// every heartbeat and the service stores and serves it. Nothing read it -- one hit in the whole
// bridge, and that a comment. A field on the wire with no reader is this repo's own recurring defect,
// which M8 was the other instance of.
//
// WHAT GOES WRONG WITHOUT IT, measured this round rather than imagined. The H4 fix has two ends: the
// service prefers a host tier over a legacy bridge, and it can only do that because aify-env sends
// `metadata.bridgeKind`. An aify-comms carrying that fix, against an aify-env too old to send it,
// silently takes the legacy path -- both sides healthy, the feature absent, and nothing saying so.
// That is the eight-day shape this project has already paid for once.
//
// A MINIMUM, NOT EQUALITY. The tiers are separate products on separate cadences -- that is the whole
// point of the three-repo split, and aify-dashboard and aify-project-graph will consume the same
// tiers. Demanding one version across all three would break the independence the split exists for.
// So aify-comms declares the OLDEST aify-env it works with, and anything newer is fine.
//
// IT NAMES NO aify-env INTERNALS, which keeps the operator's architecture constraint: a minimum
// version is a statement about THIS service's needs, not knowledge of the other tier.

/**
 * The oldest aify-env this build of aify-comms works correctly with.
 *
 * RAISED ONLY WITH A REASON, and the reason belongs next to it: a bump here tells every operator to
 * upgrade, so an unjustified one is noise that gets ignored the day it matters.
 *
 * 0.6.2 -- `metadata.bridgeKind`, which the service's supersession arbitration needs to prefer the
 * host tier over a retired environment bridge (external review, Round 8 H4). An older aify-env sends
 * no kind, so a legacy bridge that started later takes the row and becomes the only party allowed to
 * claim a spawn.
 */
export const MINIMUM_AIFY_ENV_VERSION = "0.6.2";

/** `1.2.3` -> [1,2,3]; anything unparseable -> null, which callers must treat as "cannot tell". */
export function parseVersion(text) {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(String(text || "").trim());
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

/** Negative when `a` is older than `b`, 0 when equal, positive when newer. */
export function compareVersions(a, b) {
  const left = parseVersion(a);
  const right = parseVersion(b);
  if (!left || !right) return null;
  for (let i = 0; i < 3; i += 1) {
    if (left[i] !== right[i]) return left[i] - right[i];
  }
  return 0;
}

/**
 * @param {object} deps
 * @param {Array} deps.environments   rows from GET /environments
 * @param {string} [deps.minimum]     the oldest aify-env this build works with
 * @param {(env: object) => boolean} deps.isLive  whether a row's tier is actually alive
 */
export function tierVersionVerdict({ environments = [], minimum = MINIMUM_AIFY_ENV_VERSION, isLive } = {}) {
  // ONLY LIVE ROWS. A registered-but-dead environment's version is a fact about a process that is not
  // running, and reporting it would make this row red for a host nobody is using -- the cry-wolf that
  // gets a check switched off.
  const live = environments.filter((env) => (isLive ? isLive(env) : true));
  if (!live.length) {
    return {
      ok: true,
      code: "none-live",
      detail: "no live environment tier to compare against; `env-bridge` is the row that reports that.",
      fix: "",
    };
  }

  const older = [];
  const silent = [];
  for (const env of live) {
    const kind = String(env?.metadata?.bridgeKind || "").trim().toLowerCase();
    const reported = String(env?.bridgeVersion || env?.metadata?.bridgeVersion || "").trim();

    // A ROW THAT DECLARES NO KIND IS UNVERIFIED, NOT SKIPPED, and getting this wrong is how the
    // first version of this check reported GREEN on the operator's own host while the aify-env
    // serving it was two versions behind.
    //
    // The reasoning that produced the bug was: `bridgeKind` marks the host tier, so a row without one
    // is a legacy aify-comms bridge and somebody else's problem. It is ALSO what an aify-env too old
    // to declare a kind looks like -- which is precisely the case this check exists for. Scoping to
    // rows that announce themselves meant scoping OUT every row that is behind.
    //
    // So an undeclared row is reported as no evidence. The legacy-bridge reading of it is covered by
    // its own rows, with their own remedies: H4's refusal and `bridge-current`.
    if (kind && kind !== "aify-env") continue;

    const order = compareVersions(reported, minimum);
    if (order === null) {
      silent.push(
        `${env?.id || "(no id)"}${reported ? ` reported "${reported}"` : " reported no version"}`
        + `${kind ? "" : " and no tier kind"}`,
      );
    } else if (order < 0) {
      older.push(`${env?.id || "(no id)"} is running aify-env ${reported}`);
    }
  }

  if (older.length) {
    return {
      ok: false,
      code: "tier-too-old",
      detail: `${older.join(", ")}, older than the ${minimum} this aify-comms needs. Features that `
        + "depend on the newer tier are silently absent -- both sides report healthy and the "
        + "behaviour is simply the old one.",
      fix: `Update aify-env on that host to ${minimum} or newer and restart it. Starting aify-env is `
        + "the operator's action: supersession reaps the predecessor's workers.",
    };
  }
  if (silent.length) {
    // NOT A PASS. An aify-env that reports no version is one this check could not judge, and this
    // repo has fixed green-by-default twice already (`env-bridge`, `bridge-current`).
    return {
      ok: false,
      code: "unknown-all",
      detail: `${silent.join(", ")}, so nothing here verifies the tier against the ${minimum} this `
        + "aify-comms needs. That is no evidence, not agreement.",
      fix: "Restart aify-env on that host; it reports its version on every heartbeat from then on.",
    };
  }
  return {
    ok: true,
    code: "ok",
    detail: `every live aify-env is ${minimum} or newer.`,
    fix: "",
  };
}
