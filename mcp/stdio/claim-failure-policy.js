const DEFAULT_WARN_AFTER = 3;
const DEFAULT_REPEAT_MS = 30_000;

export function claimFailureDecision({
  count,
  lastLogAt = 0,
  now = Date.now(),
  warnAfter = DEFAULT_WARN_AFTER,
  repeatMs = DEFAULT_REPEAT_MS,
} = {}) {
  const failures = Math.max(0, Number(count) || 0);
  const sustained = failures >= Math.max(1, Number(warnAfter) || DEFAULT_WARN_AFTER);
  const firstSustained = failures === Math.max(1, Number(warnAfter) || DEFAULT_WARN_AFTER);
  const repeatDue = sustained && Number(lastLogAt || 0) > 0 && now - Number(lastLogAt) > repeatMs;
  const warn = firstSustained || repeatDue;
  return {
    debug: failures === 1,
    warn,
    nextLastLogAt: warn ? now : Number(lastLogAt || 0),
  };
}

export function claimRecoveryDecision(count, { warnAfter = DEFAULT_WARN_AFTER } = {}) {
  return { log: Math.max(0, Number(count) || 0) >= Math.max(1, Number(warnAfter) || DEFAULT_WARN_AFTER) };
}
