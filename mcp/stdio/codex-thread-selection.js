// WHICH codex conversation a bridge resumes.
//
// Extracted from `runtimes-codex.js` in v0.5.4. All three were module-private there and therefore
// unreachable from a test, which is a poor place for the decision they make: pick the wrong
// thread and an agent resumes somebody else's conversation, silently, with no error anywhere.
//
// THE PATH NORMALISATION IS NOT COSMETIC, and the comment inside `pickNewestCodexThreadId`
// records why: Codex stores Windows thread cwds with backslashes while this bridge passes
// forward-slash paths, so a literal `===` comparison fell through and the cwd-matching branch
// never fired. The failure mode of that bug is not an error — it is picking the newest thread
// from the wrong directory.
//
// PREFERRED BEATS NEWEST. A thread whose cwd matches wins over a more recent one that does not,
// and only within a tier does recency decide. Sorting first and filtering second would resume the
// most recent conversation on the machine rather than the most recent one for THIS workspace.
//
// Bodies byte-identical to what stood in `runtimes-codex.js`; all three gained `export`, which is
// the only substitution.

export function parseTimestamp(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const text = String(value || "").trim();
  if (!text) return 0;
  const numeric = Number(text);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}


export function normalizePathForCompare(value) {
  return String(value || "").trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}


// `thread/list` answers under EITHER key depending on the app-server build — `threads` on some,
// `data` on others — which is why the fixture carries `FAKE_CODEX_THREAD_LIST_KEY` and defaults to
// `data`. This accessor is the one place that knows it.
//
// EXTRACTED 2026-08-17 BECAUSE THE TOLERANCE WAS NOT SHARED. `pickNewestCodexThreadId` accepted both
// shapes; `inspectCodexLiveMarker` in `runtimes-codex.js` read `listResult.threads` only. Against a
// server answering `data`, its thread array was therefore always empty, and two things silently did
// not happen: a session handle never matched a server that held it (the agent was bound by cwd guess
// instead), and — worse — the AMBIGUITY REFUSAL never fired, so two app servers holding the same
// session were not detected as ambiguous and one was picked by cwd. That refusal exists to stop an
// agent being driven through another agent's app server.
export function codexThreadListItems(listResult) {
  if (Array.isArray(listResult?.threads)) return listResult.threads;
  if (Array.isArray(listResult?.data)) return listResult.data;
  return [];
}

export function pickNewestCodexThreadId(listResult, cwd) {
  const threads = codexThreadListItems(listResult);
  if (!threads.length) return "";

  // Normalize both sides: Codex stores Windows thread cwds with backslashes,
  // but our bridge passes forward-slash paths now, so a literal === comparison
  // would silently fall through and pick the wrong thread.
  const normalizedCwd = normalizePathForCompare(cwd);
  const preferred = [];
  const fallback = [];

  for (const thread of threads) {
    const id = String(thread?.id || "").trim();
    if (!id) continue;
    const threadCwd = normalizePathForCompare(thread?.cwd || thread?.directory || thread?.worktree || "");
    if (normalizedCwd && threadCwd && threadCwd === normalizedCwd) preferred.push(thread);
    else fallback.push(thread);
  }

  const candidates = preferred.length ? preferred : fallback;
  candidates.sort((a, b) => {
    const aTime = parseTimestamp(a?.updatedAt || a?.lastUpdatedAt || a?.createdAt || a?.timestamp);
    const bTime = parseTimestamp(b?.updatedAt || b?.lastUpdatedAt || b?.createdAt || b?.timestamp);
    return bTime - aTime;
  });

  return String(candidates[0]?.id || "").trim();
}
