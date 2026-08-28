// Canonical status resolver for the Dashboard Next SPA (DASHBOARD_REBUILD_PLAN §5.2, F2):
// ONE module that maps a raw status string to its display kind/tone/label, consumed by every
// surface — never re-implemented per page. Pure (no DOM, no shared state) so it unit-tests
// directly.
//
// The table covers BOTH the 6-state proof-based agent contract (working/online/available/
// blocked/offline/stopped) AND run/contract statuses (queued/claimed/running/completed/failed/
// cancelled/lost), since the resolver serves runs and contracts too. `active`/`ready`/`idle`/
// `stale` are LEGACY aliases the proof-based engine no longer emits (idle/stale were time-decay
// states, removed 2026-06-18); they normalize here (idle→online, stale→offline) so any
// straggler from old data renders correctly instead of as a grey unknown.
import { esc, relTime } from './util.js';
import { runTargetAgent, sessionAgentId, sessionEnvironmentId, sessionId, sessionRuntime } from './record-fields.mjs';

// H1 (responsibility audit 2026-07-31): the agent status vocabulary now has ONE owner in JS.
//
// It is THE shared contract of this service — agents read it to decide whether to send, the
// dashboard filters on it, the reconciler acts on it — and it was declared authoritatively in
// `status_engine.py:19` and then hand-retyped in three more places: SESSION_FILTER_KINDS and
// SESSION_LIVE_KINDS in app.js, and an independent `new Set([...])` of the same four values in
// chat.js. Nothing bound any copy to the source, and the vocabulary is not served by the API, so
// there was no runtime path by which the client could learn it either.
//
// The failure mode was silent by construction: `resolveStatus` ends `|| STATUS_KINDS.unknown`, so a
// seventh server-side state would not throw — it would render as a muted grey "unknown" chip and
// filter into nothing. Drift with a graceful face.
//
// MUST equal `service/status_engine.py`'s VALID_STATUSES, in order. Pinned by
// `service/tests/test_status_vocabulary_binding.py`, which fails the suite on drift rather than
// letting the dashboard quietly mis-render.
// `starting` (2026-08-11) is the managed boot window: a claimed spawn whose worker has not appeared
// yet. It is LIVE — a send during boot queues and is delivered when the worker arrives — so it is
// deliberately absent from NON_LIVE_AGENT_STATUSES below. Its chip already existed here for session
// states, so the rendering needed no new design: dot 'working', tone 'warn', input disabled.
export const AGENT_STATUSES = ['working', 'online', 'available', 'blocked', 'offline', 'stopped', 'misconfigured', 'starting'];

// The subset that means "this agent can be reached right now". Derived from the list above rather
// than retyped: `offline` and `stopped` are the only non-live states, so stating the exclusion keeps
// the two definitions from drifting apart the way the two hand-typed copies did.
// `misconfigured` is NON-LIVE: the identity exists but cannot be started until a human fixes the
// config, so it must never be counted among agents you can send work to.
export const NON_LIVE_AGENT_STATUSES = ['offline', 'stopped', 'misconfigured'];
export const LIVE_AGENT_STATUSES = AGENT_STATUSES.filter((s) => !NON_LIVE_AGENT_STATUSES.includes(s));

export const STATUS_KINDS = {
  active: { label: 'online', dotKind: 'online', tone: 'ok', inputEnabled: true },
  idle: { label: 'online', dotKind: 'online', tone: 'ok', inputEnabled: true },
  available: { label: 'available', dotKind: 'available', tone: 'muted', inputEnabled: false },
  starting: { label: 'starting', dotKind: 'working', tone: 'warn', inputEnabled: false },
  recovering: { label: 'recovering', dotKind: 'working', tone: 'warn', inputEnabled: false },
  online: { label: 'online', dotKind: 'online', tone: 'ok', inputEnabled: true },
  ready: { label: 'online', dotKind: 'online', tone: 'ok', inputEnabled: true },
  working: { label: 'working', dotKind: 'working', tone: 'warn', inputEnabled: false },
  blocked: { label: 'blocked', dotKind: 'blocked', tone: 'bad', inputEnabled: false },
  stale: { label: 'offline', dotKind: 'offline', tone: 'muted', inputEnabled: false },
  queued: { label: 'queued', dotKind: 'queued', tone: 'muted', inputEnabled: false },
  claimed: { label: 'claimed', dotKind: 'working', tone: 'warn', inputEnabled: false },
  running: { label: 'running', dotKind: 'working', tone: 'warn', inputEnabled: false },
  completed: { label: 'completed', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  delivered: { label: 'delivered', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  stopped: { label: 'stopped', dotKind: 'offline', tone: 'muted', inputEnabled: false },
  // Not a transient absence — the identity cannot be started until a human fixes its config, so it
  // is presented as a WARNING rather than a muted offline: the operator has something to do.
  misconfigured: { label: 'misconfigured', dotKind: 'offline', tone: 'warn', inputEnabled: false },
  failed: { label: 'failed', dotKind: 'bad', tone: 'bad', inputEnabled: true },
  cancelled: { label: 'cancelled', dotKind: 'bad', tone: 'bad', inputEnabled: true },
  lost: { label: 'lost', dotKind: 'bad', tone: 'bad', inputEnabled: false },
  unreachable: { label: 'unreachable', dotKind: 'bad', tone: 'bad', inputEnabled: false },
  // Contract states (_contract_state) — so the work-loop chips show a meaningful dot, not grey.
  sent: { label: 'sent', dotKind: 'queued', tone: 'muted', inputEnabled: false },
  seen: { label: 'seen', dotKind: 'working', tone: 'warn', inputEnabled: false },
  missing_reply: { label: 'missing reply', dotKind: 'blocked', tone: 'bad', inputEnabled: false },
  answered: { label: 'answered', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  closed: { label: 'closed', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  overdue: { label: 'overdue', dotKind: 'blocked', tone: 'bad', inputEnabled: false },
  offline: { label: 'offline', dotKind: 'offline', tone: 'muted', inputEnabled: false },
  unknown: { label: 'unknown', dotKind: 'unknown', tone: 'muted', inputEnabled: false },
};

// Resolve a raw status into { kind, label, dotKind, tone, inputEnabled, badges }. An unknown
// raw value falls back to the `unknown` kind (never throws). Context may override the label
// and attach badge strings.
export function resolveStatus(rawStatus, context = {}) {
  const raw = String(rawStatus || '').trim().toLowerCase();
  const base = STATUS_KINDS[raw] || STATUS_KINDS.unknown;
  const label = context.label || base.label;
  const badges = Array.isArray(context.badges) ? context.badges.filter(Boolean) : [];
  return { ...base, kind: STATUS_KINDS[raw] ? raw : 'unknown', label, badges };
}

// Inline status chip with a "why" tooltip trigger (the popover handler lives in app.js).
export function renderStatusChip(rawStatus, context = {}) {
  const status = resolveStatus(rawStatus, context);
  const badges = status.badges.length ? ` <small>${esc(status.badges.join(' · '))}</small>` : '';
  const why = context.why || `${status.label} status`;
  return `<span class="status-chip ${esc(status.tone)} status-why-trigger" role="button" tabindex="0" title="${esc(why)}" data-status-why="${esc(why)}" data-tone="${esc(status.tone)}" data-status-kind="${esc(status.kind)}"><span class="status-dot ${esc(status.dotKind)}"></span>${esc(status.label)}${badges}</span>`;
}

// Bare status dot (no label) for dense rows.
export function renderStatusDot(rawStatus) {
  const status = resolveStatus(rawStatus);
  return `<span class="status-dot dot ${esc(status.dotKind)}" data-status-kind="${esc(status.kind)}" role="img" title="${esc(status.label)}" aria-label="${esc(status.label)}"></span>`;
}

// WHY a thing is in the state its chip shows — the tooltip body `renderStatusChip` renders beside the
// label. It lives here because it is the same subject as the chip: `resolveStatus` decides WHAT the status
// is, this explains it, and splitting the two across modules is how a chip ends up saying one thing and its
// explanation another. Pure: it reads records through the field readers and returns text.
export function statusWhyContext(kind, item = {}, rawStatus = item.status || 'unknown', context = {}) {
  const base = resolveStatus(rawStatus, context);
  const parts = [];
  if (kind === 'session') {
    parts.push(`Session ${sessionAgentId(item) || sessionId(item) || 'unknown'} is ${base.label}.`);
    if (sessionEnvironmentId(item)) parts.push(`Environment: ${sessionEnvironmentId(item)}.`);
    if (sessionRuntime(item)) parts.push(`Runtime: ${sessionRuntime(item)}.`);
    if (item.workspace || item.cwd) parts.push(`Workspace: ${item.workspace || item.cwd}.`);
  } else if (kind === 'run') {
    parts.push(`Run ${item.id || 'unknown'} is ${base.label}.`);
    if (runTargetAgent(item)) parts.push(`Target: ${runTargetAgent(item)}.`);
    if (item.requestedAt) parts.push(`Requested ${relTime(item.requestedAt)} ago.`);
    if (item.startedAt) parts.push(`Started ${relTime(item.startedAt)} ago.`);
    if (item.error || item.blockedByActiveRun) parts.push(`Reason: ${item.error || item.blockedByActiveRun}.`);
  } else if (kind === 'contract') {
    parts.push(`Work Loop item ${item.subject || item.id || 'unknown'} is ${base.label}.`);
    if (item.targetAgentId) parts.push(`Target: ${item.targetAgentId}.`);
    if (item.lastReminderAt) parts.push(`Last reminder ${relTime(item.lastReminderAt)} ago.`);
    if (item.overdue) parts.push('It is overdue.');
  } else if (kind === 'agent') {
    parts.push(`Agent ${item.id || 'unknown'} is ${base.label}.`);
    if (item.runtime) parts.push(`Runtime: ${item.runtime}.`);
    if (item.statusNote) parts.push(`Note: ${item.statusNote}.`);
    if (item.lastSeen || item.last_seen) parts.push(`Last seen ${relTime(item.lastSeen || item.last_seen)} ago.`);
  } else if (kind === 'environment') {
    parts.push(`Environment ${item.label || item.id || 'unknown'} is ${base.label}.`);
    if (item.bridgeId || item.bridge_id) parts.push(`Bridge: ${item.bridgeId || item.bridge_id}.`);
    if (item.lastSeen || item.last_seen) parts.push(`Last heartbeat ${relTime(item.lastSeen || item.last_seen)} ago.`);
  } else {
    parts.push(`${kind || 'Item'} is ${base.label}.`);
  }
  return { ...context, label: context.label || base.label, why: parts.filter(Boolean).join(' ') };
}
export function runStatusContext(run) {
  const blockerReason = String(run?.blockedByActiveRun || run?.blockedBy || run?.error || '').trim();
  return {
    label: run?.status || 'unknown',
    blockerReason,
    badges: blockerReason && resolveStatus(run?.status).kind === 'blocked' ? ['blocked'] : [],
  };
}
