// Process-local partial evidence is diagnostic, never switch qualification.
const SCOPE = "partial-freshness-base-word";
const PURPOSES = ["status word", "busy", "send-time queue", "claim", "worker readiness", "live counts", "reminder skip (B21)", "stranded-reply guard (B26)", "running-run promotion", "stale-turn ceiling"];
const SOURCES = ["refresh", "cache-broadcast", "engine-status"];
const COUNTS = ["observed", "nonEvaluation", "evaluated", "partialCompared", "partialAgreed", "partialDisagreed", "unavailable"];
const GAPS = ["turn.ageMs", "runtime provenance", "roster gates", "running-run promotion", "other nine C8 owners"];
// Schema-1 recorder catalogs in service/api_core/partial_status_shadow.py.
const CLASSES = ["partial-agree", "partial-disagree", "unavailable", "non-evaluation", "unknown-loss"];
const REASONS = ["unknown-knowledge", "publisher-unavailable", "missing-row", "ambiguous-association", "stale", "invalid-clock", "future", "unknown-state", "unknown-loss"];
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const exactKeys = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const count = value => Number.isSafeInteger(value) && value >= 0;
const copyCounts = value => Object.fromEntries(COUNTS.map(key => [key, value[key]]));
function validCounts(value) {
  return exactKeys(value, COUNTS) && COUNTS.every(key => count(value[key]))
    && value.observed === value.nonEvaluation + value.evaluated
    && value.evaluated === value.partialCompared + value.unavailable
    && value.partialCompared === value.partialAgreed + value.partialDisagreed;
}

function validReport(report) {
  if (!object(report) || report.schemaVersion !== 1 || report.scope !== SCOPE || report.window !== "process"
      || report.coverageComplete !== false || report.qualification !== "UNAVAILABLE" || report.fullQualifiedComparisons !== 0
      || !exactKeys(report.versions, ["env", "wrapper", "comms"]) || Object.values(report.versions).some(v => v !== null)
      || !Array.isArray(report.qualificationGaps) || !report.qualificationGaps.every(gap => typeof gap === "string")
      || !GAPS.every(gap => report.qualificationGaps.includes(gap))
      || !exactKeys(report.byPurpose, PURPOSES)) return false;
  for (const purpose of PURPOSES) {
    const row = report.byPurpose[purpose];
    if (!object(row)) return false;
    if (purpose !== "status word") {
      if (row.instrumented !== false || row.comparisonScope !== "not-instrumented"
          || row.counts !== null || row.bySource !== null || row.neverObservedClasses !== null) return false;
      continue;
    }
    if (row.instrumented !== true || row.comparisonScope !== SCOPE || !validCounts(row.counts)
        || !exactKeys(row.bySource, SOURCES) || !SOURCES.every(source => validCounts(row.bySource[source]))
        || !Array.isArray(row.neverObservedClasses) || !row.neverObservedClasses.every(v => CLASSES.includes(v))
        || new Set(row.neverObservedClasses).size !== row.neverObservedClasses.length
        || !object(row.unavailableByReason) || !Object.keys(row.unavailableByReason).every(reason => REASONS.includes(reason))
        || !Object.values(row.unavailableByReason).every(count)) return false;
    for (const key of COUNTS) {
      const total = SOURCES.reduce((sum, source) => sum + row.bySource[source][key], 0);
      if (!count(total) || total !== row.counts[key]) return false;
    }
  }
  return true;
}

// Whitelist projection. No raw observation, reason, configuration or identifier is copied.
function boundedShadow(report) {
  return {
    schemaVersion: 1, scope: SCOPE, window: "process", coverageComplete: false,
    qualification: "UNAVAILABLE", fullQualifiedComparisons: 0,
    versions: { env: null, wrapper: null, comms: null }, qualificationGaps: [...GAPS],
    byPurpose: Object.fromEntries(PURPOSES.map(purpose => {
      const row = report.byPurpose[purpose];
      return [purpose, {
        instrumented: row.instrumented, comparisonScope: row.comparisonScope,
        counts: row.counts === null ? null : copyCounts(row.counts),
        bySource: row.bySource === null ? null : Object.fromEntries(SOURCES.map(source => [source, copyCounts(row.bySource[source])])),
      }];
    })),
  };
}

/** Pure fail-closed verdict over the schema-1 partial report. */
export function shadowReportVerdict(report) {
  if (!validReport(report)) return { ok: false, code: "skipped", detail: "Shadow report unreadable or malformed; qualification UNAVAILABLE." };
  const shadow = boundedShadow(report);
  const c = shadow.byPurpose["status word"].counts;
  const detail = `${c.partialCompared} partial comparisons: ${c.partialAgreed} agreed, ${c.partialDisagreed} disagreed; ${c.unavailable} unavailable; ${c.observed} observed, ${c.nonEvaluation} non-evaluations. Qualification UNAVAILABLE: ${GAPS.join(", ")}.`;
  return { ok: false, code: c.partialDisagreed > 0 ? "partial" : "skipped", detail, shadow };
}

/** One read via doctor's existing authenticated GET; no default I/O collaborators. */
export async function checkAgentStateShadow({ get, add, skip }) {
  let report = null;
  try { report = await get("/api/v1/agent-state/shadow-report"); } catch { /* no raw transport error in report */ }
  const verdict = shadowReportVerdict(report);
  if (verdict.code === "skipped") return skip("agent-state-shadow", verdict.detail, verdict.shadow, verdict.ok);
  return add("agent-state-shadow", verdict.ok, verdict.code, verdict.detail, "", verdict.shadow);
}
