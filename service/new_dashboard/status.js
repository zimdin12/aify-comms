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
import { esc } from './util.js';

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
