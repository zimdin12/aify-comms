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
import { state } from './state.mjs';
import { resolveStatus } from './status.js';
import { byId, toast } from './ui.js';
import { esc, relTime } from './util.js';

export function openAgentEditForm(agentId) {
  const agent = state.agents.find((a) => a.id === agentId) || { id: agentId };
  const currentEnv = String((agent.runtimeState && agent.runtimeState.environmentId) || (agent.runtimeConfig && agent.runtimeConfig.environmentId) || '');
  const currentRuntime = String(agent.runtime || 'generic');
  const onlineEnvs = state.environments.filter((env) => resolveStatus(env.status).kind === 'online');
  const envOptions = ['<option value="">— keep current —</option>']
    .concat(onlineEnvs.map((env) => {
      const id = String(env.id || env.environmentId || '');
      return `<option value="${esc(id)}"${id === currentEnv ? ' selected' : ''}>${esc(env.label || id)}</option>`;
    })).join('');
  const runtimeOptions = [...new Set(['generic', 'claude-code', 'codex', 'hermes', 'pi', 'opencode', currentRuntime])]
    .map((rt) => `<option value="${esc(rt)}"${rt === currentRuntime ? ' selected' : ''}>${esc(rt)}</option>`).join('');
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>Edit ${esc(agentId)}</strong></div>
      <label class="settings-label">Agent ID (rename)<input id="edit-agent-id" type="text" value="${esc(agentId)}"></label>
      <label class="settings-label">Description<input id="edit-agent-desc" type="text" value="${esc(agent.description || '')}" placeholder="Short role/description"></label>
      <label class="settings-label">Native session handle<input id="edit-agent-handle" type="text" value="${esc(agent.sessionHandle || agent.session_handle || '')}" placeholder="Claude/Codex/Pi session id — blank clears"></label>
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
