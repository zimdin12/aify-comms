// The Sessions page activity feed: what this agent has been doing, runs and messages interleaved.
//
// Extracted from app.js in v0.5.4 as a measured closure — three declarations needing only sibling leaf
// modules, imported downward. `runFrom` and `messagesForSession` come with it because nothing outside the
// closure reads them; the ownership test throughout this series is a count of DIRECT readers.
//
// The merge is the part worth a test. A run belongs to this feed if the agent is its TARGET **or** its
// sender — dropping either half hides work the operator is looking straight at — and runs and messages are
// then interleaved by timestamp, newest first, capped at 60. Timestamps are parsed defensively: an
// unparseable one sorts as 0 rather than poisoning the comparison with NaN, which would leave the order
// undefined for every row.
//
// NOT the Work Loop activity feed (`work-loop-panels.mjs`), which merges runs, messages AND contracts
// across the whole fleet and caps at 10. This one is per-session and per-agent. They look alike and are
// different subjects; keeping them in separate modules is deliberate.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Their leading comments stayed behind
// in app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its
// comments could not round-trip through the proof.

export function messagesForSession(session) {
  const agentId = sessionAgentId(session);
  if (!agentId) return [];
  return state.messages
    .filter((message) => message.from === agentId || message.to === agentId || message.targetAgentId === agentId || message.target_agent_id === agentId)
    .slice(0, 50);
}
export function renderSessionActivity(session) {
  const agentId = sessionAgentId(session);
  const host = byId('session-activity');
  if (!host) return;
  const ts = (v) => { const n = Date.parse(String(v || '')); return Number.isFinite(n) ? n : 0; };
  const runItems = state.runs
    .filter((r) => runTargetAgent(r) === agentId || runFrom(r) === agentId)
    .map((r) => ({ kind: 'run', ts: ts(r.updatedAt || r.createdAt || r.created_at), r }));
  const msgItems = messagesForSession(session)
    .map((m) => ({ kind: 'msg', ts: ts(m.timestamp || m.createdAt), m }));
  const items = [...runItems, ...msgItems].sort((a, b) => b.ts - a.ts).slice(0, 60);
  host.innerHTML = items.length ? items.map((it) => {
    if (it.kind === 'run') {
      const r = it.r;
      return `<article class="activity-row" data-kind="run" data-id="${esc(r.id)}">
        <div class="item-title"><span class="button-row">${renderStatusChip(r.status, statusWhyContext('run', r, r.status))}<strong class="clip">${esc(r.subject || r.id)}</strong></span>
          <button class="ghost" data-run-inspector="${esc(r.id)}" data-run-source="activity">Inspect</button></div>
        ${r.summary || r.error ? `<p class="preview">${esc(r.summary || r.error)}</p>` : ''}
      </article>`;
    }
    const m = it.m; const id = messageId(m);
    return `<article class="activity-row" data-kind="message" data-id="${esc(id)}">
      <div class="item-title"><strong>${esc(m.from || 'unknown')}</strong>${renderStatusChip(m.read ? 'completed' : 'queued', { label: m.type || (m.read ? 'read' : 'unread'), why: `Message ${m.read ? 'read' : 'unread'}.` })}</div>
      <p class="preview">${esc(m.subject ? m.subject + ' — ' : '')}${esc(m.body || m.preview || '')}</p>
    </article>`;
  }).join('') : '<div class="empty-state"><span class="empty-icon">📋</span><strong>No activity yet</strong><p>Runs and messages for this session appear here. Use Chat to message the agent.</p></div>';
}

import { messageId, runTargetAgent, sessionAgentId } from './record-fields.mjs';
import { state } from './state.mjs';
import { renderStatusChip, statusWhyContext } from './status.js';
import { byId } from './ui.js';
import { esc } from './util.js';

export const runFrom = (r) => String(r.from || r.fromAgent || r.from_agent || '');
