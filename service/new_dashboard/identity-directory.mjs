// The identity directory: every registered agent in one table, with its mode, runtime, environment and
// unread count.
//
// The LAST slice out of app.js that needs no ruling. Everything else still in that file reaches `apiBase`
// through the `api()` fetch wrapper, and `apiBase` is evaluated at module load from `location` and
// `localStorage` — moving it would make its module, and every module importing it, unloadable outside a
// browser. That is a decision, not a script; see docs/APP_JS_APIBASE_PACKET.md.
//
// One declaration, and that is not a sign it should have stayed. `environment-start-command.mjs`,
// `terminal-width.mjs` and `inspector-refresh.mjs` are single-purpose modules for the same reason: a
// function only becomes testable once it leaves app.js, which is reachable only by source regex. The
// counts this renders — how many agents are managed versus resident, and the unread total — are summary
// arithmetic over live fleet state, and nothing has ever checked them.
//
// The declaration is byte-identical to the one that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Its leading comments stayed behind in
// app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its comments
// could not round-trip through the proof.


import { sessionForAgent } from './agent-drawer.mjs';
import { sessionEnvironmentId, sessionRuntime } from './record-fields.mjs';
import { state } from './state.mjs';
import { renderStatusChip, statusWhyContext } from './status.js';
import { byId } from './ui.js';
import { esc, relTime } from './util.js';

export function openIdentityDirectory() {
  const agents = [...state.agents].sort((a, b) => String(a.id || '').localeCompare(String(b.id || '')));
  const modeOf = (agent, session) => String(agent.sessionMode || (session && (session.mode || session.session_mode)) || 'resident').toLowerCase();
  const managed = agents.filter((a) => modeOf(a, sessionForAgent(a.id)) === 'managed').length;
  const resident = agents.length - managed;
  const unread = agents.reduce((sum, a) => sum + Number(a.unread || a.unreadCount || 0), 0);
  const rows = agents.map((agent) => {
    const id = String(agent.id || '');
    const session = sessionForAgent(id);
    const mode = modeOf(agent, session);
    const env = session ? (state.environments.find((e) => String(e.id) === String(sessionEnvironmentId(session))) || null) : null;
    const envLabel = (env && (env.label || env.id)) || (session ? sessionEnvironmentId(session) : '') || '';
    const runtime = agent.runtime || (session && sessionRuntime(session)) || '';
    const lastSeen = agent.lastSeen || '';
    return `<tr>
      <td><strong>${esc(id)}</strong></td>
      <td>${esc(agent.role || '')}</td>
      <td>${esc(runtime || '—')}</td>
      <td>${esc(mode)}</td>
      <td class="clip">${esc(envLabel || '—')}</td>
      <td>${renderStatusChip(agent.status || 'unknown', statusWhyContext('agent', agent, agent.status))}</td>
      <td>${Number(agent.unread || 0) || 0}</td>
      <td>${lastSeen ? esc(relTime(lastSeen)) + ' ago' : '—'}</td>
      <td class="identity-row-actions">
        <button class="ghost" data-agent-details="${esc(id)}" title="Open the agent detail drawer (lifecycle controls)">Details</button>
        <button class="ghost danger" data-agent-remove="${esc(id)}" title="Unregister/forget this identity (tombstones it)">Remove</button>
      </td>
    </tr>`;
  }).join('');
  const table = agents.length
    ? `<div class="table-wrap"><table class="identity-table"><thead><tr>
        <th>ID</th><th>Role</th><th>Runtime</th><th>Mode</th><th>Environment</th><th>Status</th><th>Unread</th><th>Last seen</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="empty-state"><span class="empty-icon">🪪</span><strong>No identities</strong><p>No agents are registered yet.</p></div>';
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer identity-directory">
      <div class="agent-drawer-head"><strong>Identity directory</strong></div>
      <p class="subtle">Identities are the stable mailbox, role, and routing behind chat. Use this directory to audit roles, runtime, session mode, bound environment, and live status — or to forget an offline CLI identity. Runtime control lives on Sessions.</p>
      <dl class="chat-kv agent-drawer-kv identity-directory-stats">
        <dt>Managed</dt><dd>${managed}</dd>
        <dt>Resident / manual</dt><dd>${resident}</dd>
        <dt>Total unread</dt><dd>${unread}</dd>
      </dl>
      ${table}
    </div>`;
  state.inspector = { ...state.inspector, kind: 'identity-directory', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}
