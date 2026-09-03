// The inspector's FORM and DETAIL panels: editing an agent, continuing a session, reading one message.
//
// Distinct from `agent-drawer.mjs`, which owns the agent OVERVIEW and the selection sync that keeps it
// pointed at the right agent. These three are what the same drawer shows INSTEAD of that overview when the
// operator asks for something specific, and two of them hold operator input.
//
// That shared property is why they belong together rather than scattered: a panel holding half-typed input
// has the same hazard as the settings form and the spawn dropdowns — the ~15s poll re-renders, and
// rebuilding underneath the operator destroys what they were writing. Keeping them in one module makes that
// class of bug visible in one place instead of three.
//
// `buildHandoffPacket` comes with `openContinueForm` because nothing else reads it: it assembles the
// session context a continuation needs, and it exists only to fill that form.
//
// Extracted from app.js in v0.5.4 as a measured closure needing only sibling leaf modules, imported
// downward. It became possible once `state` and `byId` had owners; before that every panel in app.js read
// at least one name app.js itself declared, which a module extracted from app.js cannot import back.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `. Their leading comments stayed behind — `declarationSpan` returns the declaration alone, so a
// span carrying its comments could not round-trip through the reconstruction proof.


import { messageId, messageRunId, sessionAgentId, sessionEnvironmentId, sessionId, sessionRuntime } from './record-fields.mjs';
import { api } from './api-client.mjs';
import { state } from './state.mjs';
import { renderStatusChip, resolveStatus, spawnClaim } from './status.js';
import { byId, toast } from './ui.js';
import { esc, relTime } from './util.js';

export function openAgentEditForm(agentId) {
  const agent = state.agents.find((a) => a.id === agentId) || { id: agentId };
  const currentEnv = String((agent.runtimeState && agent.runtimeState.environmentId) || (agent.runtimeConfig && agent.runtimeConfig.environmentId) || '');
  const currentRuntime = String(agent.runtime || 'generic');
  const onlineEnvs = state.environments.filter((env) => resolveStatus(env.status).kind === 'online');
  const envOptions = ['<option value="">— keep current —</option>']
    .concat(onlineEnvs.map((env) => {
      const id = String(env.id || '');
      // NAMED, NOT REMOVED. Moving an agent to a host where nothing claims leaves it unable to
      // start, and `online` is aify-env describing the machine rather than offering to run it --
      // the same conflation that cost a day on 2026-09-02. Dropping the option would hide the
      // reason and refuse a host whose claimer the operator is about to start.
      const note = spawnClaim(env).canSpawn ? '' : ' — cannot spawn';
      return `<option value="${esc(id)}"${id === currentEnv ? ' selected' : ''}>${esc(env.label || id)}${note}</option>`;
    })).join('');
  const runtimeOptions = [...new Set(['generic', 'claude-code', 'codex', 'hermes', 'pi', 'opencode', currentRuntime])]
    .map((rt) => `<option value="${esc(rt)}"${rt === currentRuntime ? ' selected' : ''}>${esc(rt)}</option>`).join('');
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>Edit ${esc(agentId)}</strong></div>
      <label class="settings-label">Agent ID (rename)<input id="edit-agent-id" type="text" value="${esc(agentId)}"></label>
      <label class="settings-label">Description<input id="edit-agent-desc" type="text" value="${esc(agent.description || '')}" placeholder="Short role/description"></label>
      <label class="settings-label">Native session handle<input id="edit-agent-handle" type="text" value="${esc(agent.sessionHandle || '')}" placeholder="Claude/Codex/Pi session id — blank clears"></label>
      <fieldset class="agent-edit-env">
        <legend>Re-assign environment</legend>
        <label class="settings-label">Environment<select id="edit-agent-env">${envOptions}</select></label>
        <label class="settings-label">Runtime<select id="edit-agent-runtime">${runtimeOptions}</select></label>
        <label class="settings-label">Workspace (optional)<input id="edit-agent-workspace" type="text" value="${esc(agent.cwd || '')}" placeholder="Leave blank for the environment default root"></label>
        <p class="subtle">Only takes effect when an environment is chosen above. The environment must be online and advertise the selected runtime.</p>
      </fieldset>
      <div class="agent-drawer-actions"><button class="primary" data-agent-edit-submit="${esc(agentId)}">Save changes</button></div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'agent-edit', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}
export function openMessageDetail(msgId) {
  const m = state.messages.find((x) => messageId(x) === String(msgId));
  if (!m) { toast('Message not found in the loaded set', 'warn'); return; }
  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value}</dd>`;
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer">
      <div class="agent-drawer-head"><strong>Message ${esc(String(messageId(m)).slice(0, 12))}</strong></div>
      <dl class="chat-kv agent-drawer-kv">
        ${row('From', esc(m.from || 'unknown'))}
        ${row('To', esc(m.to || m.target || '—'))}
        ${row('Type', esc(m.type || 'info'))}
        ${row('Priority', esc(m.priority || 'normal'))}
        ${row('Read', m.read === false ? 'unread' : 'read')}
        ${row('When', esc(relTime(m.timestamp || m.createdAt)) + ' ago')}
        ${messageRunId(m) ? row('Run', esc(messageRunId(m))) : ''}
      </dl>
      ${m.subject ? `<h4 class="an-h">${esc(m.subject)}</h4>` : ''}
      <p class="chat-msg-body">${esc(m.body || m.preview || '')}</p>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'message', runId: '', messageId: msgId };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}
export function buildHandoffPacket(agentId, count = 25) {
  const related = state.messages
    .filter((m) => m.from === agentId || m.to === agentId || m.target === agentId)
    .slice(-count)
    .map((m) => `[${m.from || '?'}→${m.to || m.target || '?'}] ${m.subject ? m.subject + ': ' : ''}${m.body || m.preview || ''}`.trim());
  return `Handoff packet for ${agentId} (last ${related.length} messages):\n\n${related.join('\n')}`;
}
export function openContinueForm(sid, splitIdentity) {
  const target = state.sessions.find((s) => String(sessionId(s)) === String(sid));
  if (!target) { toast('Session not found', 'warn'); return; }
  const agentId = sessionAgentId(target) || '';
  const packet = buildHandoffPacket(agentId);
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>${splitIdentity ? 'Continue as new agent' : 'Compact session'}</strong></div>
      <p class="subtle">${splitIdentity ? 'Splits into a new agent identity with an editable handoff packet.' : 'Compacts into a fresh managed backing, keeping the same agent ID.'}</p>
      <label class="settings-label">Agent ID<input id="cont-agent-id" type="text" value="${esc(splitIdentity ? '' : agentId)}" placeholder="${esc(agentId)}"></label>
      <label class="settings-label">Role<input id="cont-role" type="text" value="${esc(target.role || 'coder')}"></label>
      <label class="settings-label">Environment<input id="cont-env" type="text" value="${esc(sessionEnvironmentId(target) || '')}"></label>
      <label class="settings-label">Runtime<input id="cont-runtime" type="text" value="${esc(sessionRuntime(target) || '')}"></label>
      <label class="settings-label">Workspace<input id="cont-workspace" type="text" value="${esc(target.workspace || target.cwd || '')}"></label>
      <label class="settings-label">Handoff packet<textarea id="cont-packet" rows="8">${esc(packet)}</textarea></label>
      <div class="agent-drawer-actions">
        <button class="primary" data-continue-submit="${esc(sid)}" data-split="${splitIdentity ? '1' : '0'}">${splitIdentity ? 'Continue as' : 'Compact'}</button>
      </div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'continue', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}

// The compaction/spawn HISTORY panel — the fourth thing the same drawer shows on request, and the only
// one that fetches. Extracted from app.js in v0.5.4, joining the owner of the other inspector panels
// rather than starting a parallel module for one more of them.

/**
 * What a spawn record says about where the agent came from.
 *
 * THE METADATA IS NESTED AND THIS PANEL READ IT FLAT. `_spawn_request_to_dict` emits no `metadata`
 * key at all -- the spec's metadata arrives as `spawnSpec.metadata`. So `const m = r.metadata || {}`
 * was ALWAYS `{}`, and everything it drove was dead: the mode label fell through to "Spawn" for every
 * record, both lineage rows were never rendered, and the filter's `continuedFromAgentId` arm could
 * never match. Measured against the live service on 2026-08-26 over 200 spawn records: top-level
 * `metadata` on 0 of them, `spawnSpec.metadata` on 149, `compactMode` on 82, and the three
 * continue-as keys on 10 each. The panel is subtitled "Compact/continue lineage from spawn records"
 * and could not show lineage for any of the 92 records that had it.
 *
 * BOTH LINEAGE VOCABULARIES, because the panel claims both. A continue-as record carries
 * `continuedFrom*`; a compaction carries `compactedFrom*` (72 of the 200), which nothing here read
 * even by the right path. Reading only the first would relabel those 82 records correctly and still
 * show them with no origin.
 *
 * `r.metadata` is still consulted as a fallback: it costs one `??` and means a future serialiser that
 * DOES flatten the field needs no change here.
 */
export function spawnRecordLineage(record = {}) {
  const meta = (record.spawnSpec && record.spawnSpec.metadata) || record.metadata || {};
  const mode = meta.splitIdentity
    ? 'Continue-as'
    : meta.compactMode === 'handoff' ? 'Compact' : 'Spawn';
  // WHO ASKED. `createdBy` is serialised onto every spawn request and nothing rendered it, which left
  // the panel unable to answer the one question an operator brings to it: I did not start this, so who
  // did? MEASURED on the live database, the answer is usually the agent itself -- of the six spawn
  // requests on 2026-08-26 for the three long-running hermes agents, three were `dashboard` (the
  // operator) and three named the agent being spawned, about fifty seconds later. An agent re-spawning
  // itself and an operator restarting it look identical in this panel, and send you to opposite places.
  const requestedBy = String(record.createdBy || record.created_by || '').trim();
  const subject = String(record.agentId || record.agent_id || '').trim();
  return {
    mode,
    requestedBy,
    // Called out rather than left for the reader to compare two ids, because that comparison is the
    // whole finding and it is easy to skim past.
    selfRequested: Boolean(requestedBy) && requestedBy === subject,
    fromAgentId: meta.continuedFromAgentId || meta.compactedFromAgentId || '',
    fromSessionId: meta.continuedFromSessionId || meta.compactedFromSessionId || '',
  };
}

export async function openCompactionHistory(agentId) {
  byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Loading…</p></div>`;
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
  state.inspector = { ...state.inspector, kind: 'history', runId: '', agentId };
  let rows = [];
  try {
    const res = await api('/spawn-requests');
    const reqs = res.spawnRequests || res.requests || res || [];
    rows = (Array.isArray(reqs) ? reqs : []).filter((r) => {
      // An agent's history is the records it CAME FROM as well as the ones that produced it.
      const { fromAgentId } = spawnRecordLineage(r);
      return fromAgentId === agentId || r.agentId === agentId || r.agent_id === agentId;
    }).sort((a, b) => String(b.createdAt || b.created_at || '').localeCompare(String(a.createdAt || a.created_at || '')));
  } catch (err) {
    byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Could not load spawn records: ${esc(String(err?.message || err))}</p></div>`;
    return;
  }
  const body = rows.length ? rows.map((r) => {
    const { mode, fromAgentId, fromSessionId, requestedBy, selfRequested } = spawnRecordLineage(r);
    return `<div class="history-row">
      <div class="history-head"><strong>${esc(mode)}</strong>${renderStatusChip(r.status || 'queued', { label: r.status || 'queued', why: `Spawn request ${r.status || 'queued'}.` })}</div>
      <dl class="agent-drawer-kv">
        <dt>When</dt><dd>${esc(relTime(r.createdAt || r.created_at))} ago</dd>
        <dt>New agent</dt><dd>${esc(r.agentId || r.agent_id || '—')}</dd>
        <dt>Requested by</dt><dd>${esc(requestedBy || 'not recorded')}${selfRequested ? ' <span class="subtle">(itself)</span>' : ''}</dd>
        ${fromAgentId ? `<dt>From agent</dt><dd>${esc(fromAgentId)}</dd>` : ''}
        ${fromSessionId ? `<dt>From session</dt><dd class="clip">${esc(fromSessionId)}</dd>` : ''}
        ${r.subject ? `<dt>Subject</dt><dd class="clip">${esc(r.subject)}</dd>` : ''}
      </dl></div>`;
  }).join('') : '<div class="empty-state"><span class="empty-icon">🕮</span><strong>No history</strong><p>No compaction or continuation records found for this agent.</p></div>';
  byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Compact/continue lineage from spawn records.</p>${body}</div>`;
}
