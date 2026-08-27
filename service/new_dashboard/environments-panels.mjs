// The Environments page: the environment list, the spawn-request queue, and the spawn form's options.
//
// One subject, three renderers, extracted from app.js in v0.5.4 as a measured closure needing only sibling
// leaf modules imported downward.
//
// `renderSpawnRequests` is the one with history behind it: it was ported from the 8800 dashboard because
// claimed/failed/done spawn requests had nowhere to appear, so a spawn that failed or stuck was invisible
// unless someone read the database. It also aliases `done`, the one spawn status the canonical resolver
// does not know — an alias that silently stopped working would put every completed spawn back into the
// unknown bucket, which is the failure this module's tests exist to catch.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Their leading comments stayed behind
// in app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its
// comments could not round-trip through the proof.

export function renderEnvironmentSpawnOptions(selectedEnvId = byId('env-spawn-environment')?.value || '') {
  const envSelect = byId('env-spawn-environment');
  const runtimeSelect = byId('env-spawn-runtime');
  if (!envSelect || !runtimeSelect) return;
  // Don't rebuild the spawn dropdowns while the operator is interacting with the form — the 15s
  // poll would otherwise reset an open/selected dropdown (deep-audit C minor).
  if (byId('environment-spawn-form')?.contains(document.activeElement)) return;
  const currentEnv = state.environments.some((env) => String(env.id) === selectedEnvId)
    ? selectedEnvId
    : String(state.environments.find((env) => resolveStatus(env.status).kind === 'online')?.id || state.environments[0]?.id || '');
  envSelect.innerHTML = '<option value="">Environment</option>' + state.environments.map((env) => `<option value="${esc(env.id)}"${String(env.id) === currentEnv ? ' selected' : ''}>${esc(env.label || env.id)} (${esc(resolveStatus(env.status).label)})</option>`).join('');
  const env = state.environments.find((item) => String(item.id) === currentEnv) || {};
  const runtimeOptions = environmentRuntimes(env);
  const available = runtimeOptions.filter((runtime) => runtime.available !== false);
  // Preserve the operator's runtime pick across poll re-renders (review finding #9):
  // the focus guard above only protects while focus is INSIDE the form — pick a runtime,
  // click elsewhere, and the next poll silently reset it to available[0] right before Spawn.
  const priorRuntime = runtimeSelect.value || '';
  runtimeSelect.innerHTML = '<option value="">Runtime</option>' + runtimeOptions.map((runtime) => {
    const disabled = runtime.available === false ? ' disabled' : '';
    const suffix = runtime.available === false ? ' (unavailable)' : '';
    return `<option value="${esc(runtime.runtime)}"${disabled}>${esc(runtime.runtime)}${suffix}</option>`;
  }).join('');
  runtimeSelect.value = available.some((r) => r.runtime === priorRuntime) ? priorRuntime : (available[0]?.runtime || '');
  const workspace = byId('env-spawn-workspace');
  if (workspace && !workspace.value) workspace.value = environmentRoots(env)[0] || '';
}

import { api } from './api-client.mjs';
import { environmentStartCommand } from './environment-start-command.mjs';
import { asArray, environmentRoots, environmentRuntimes } from './record-fields.mjs';
import { state } from './state.mjs';
import { renderStatusChip, resolveStatus, statusWhyContext } from './status.js';
import { metric } from './summary-tiles.mjs';
import { byId, toast, uiConfirm } from './ui.js';
import { esc, relTime } from './util.js';

/**
 * ` · last seen 83d ago`, for an environment that is not answering. Empty for everything else.
 *
 * WHY AN OFFLINE PILL IS NOT ENOUGH. A host that dropped a minute ago and one abandoned in June render
 * identically as `offline`, and those call for opposite actions -- wait, versus Forget. The age is the
 * only thing that tells them apart and it was already on the wire: `/environments` carries `lastSeen`
 * for every row, and this card dropped it. On the operator's fleet 2026-08-27 the WSL environment had
 * been silent since 2026-06-05 and the card said only `offline`.
 *
 * IT FAILS CLOSED. `relTime` returns '' for a missing or unparseable timestamp, so a row with no
 * lastSeen makes no claim about its age rather than rendering `last seen  ago` or, worse, an age
 * measured from the epoch.
 */
function offlineAge(env) {
  if (resolveStatus(env.status).kind !== 'offline') return '';
  const seen = relTime(env.lastSeen || env.last_seen);
  return seen ? ` · last seen ${esc(seen)} ago` : '';
}

export function renderRuntime() {
  byId('environment-list').innerHTML = state.environments.map((env) => `
    <article class="runtime-card" data-kind="environment" data-id="${esc(env.id)}">
      <div class="item-title"><strong>${esc(env.label || env.id)}</strong>${renderStatusChip(env.status, statusWhyContext('environment', env, env.status))}</div>
      <p class="preview">${esc(env.kind || env.os || '')} · ${esc(env.machineId || env.machine_id || '')}${offlineAge(env)}</p>
      <div class="env-runtime-list">
        ${environmentRuntimes(env).map((runtime) => `<span class="env-runtime-pill${runtime.available === false ? ' unavailable' : ''}">${esc(runtime.runtime)}${runtime.available === false ? ' (unavailable)' : ''}</span>`).join('') || '<span class="env-runtime-pill unavailable">no runtimes</span>'}
      </div>
      <div class="env-root-list">
        ${(() => { const roots = environmentRoots(env); const shown = roots.slice(0, 4).map((root) => `<code>${esc(root)}</code>`).join(''); const more = roots.length > 4 ? `<span class="subtle">+${roots.length - 4} more</span>` : ''; return (shown + more) || '<span class="subtle">No workspace roots advertised</span>'; })()}
      </div>
      <div class="contract-actions">
        ${resolveStatus(env.status).kind === 'offline' ? '' : `<button class="ghost" data-env-spawn="${esc(env.id)}" title="Open the spawn form prefilled for this environment">Spawn here…</button>`}
        <button class="ghost" data-env-roots="${esc(env.id)}" title="Edit the workspace roots agents may be spawned into">Edit roots…</button>
        ${resolveStatus(env.status).kind === 'offline'
          ? `<button class="ghost danger" data-env-control="forget" data-env-id="${esc(env.id)}" title="Hide this offline environment (identities/chats/records remain)">Forget</button>`
          : `<button class="ghost danger" data-env-control="stop" data-env-id="${esc(env.id)}" title="Ask this host bridge process to exit">Stop bridge</button>`}
      </div>
    </article>`).join('') || '<div class="empty-state"><span class="empty-icon">🔌</span><strong>No environments connected</strong><p>Start an aify-comms bridge on a host to see it here.</p></div>';
}
/**
 * Fetch the spawn requests this page renders, and draw them.
 *
 * The poll only asks for them while the Environments page is open (see `shouldLoadForPage`), because
 * the slice is the largest thing a cycle moves -- 414,690 of 1,419,728 bytes measured on 2026-08-26 --
 * and this table is its only reader. That makes OPENING the page the moment the data is needed, and
 * the next poll is up to a full refresh interval away (15s by default), which on a first open would
 * show "No spawn requests" for a table that has plenty.
 *
 * Errors are swallowed on purpose and the previous list kept: this runs on a navigation, and a failed
 * fetch should leave the page as it was rather than empty it. The poll reports its own slice failures.
 */
export async function loadSpawnRequests() {
  try {
    const res = await api('/spawn-requests?limit=200');
    state.spawnRequests = asArray(res, 'spawnRequests');
  } catch (_) { /* keep the prior list */ }
  renderSpawnRequests();
}

export function renderSpawnRequests() {
  const el = byId('spawn-requests-list');
  if (!el) return;
  const requests = [...state.spawnRequests].sort((a, b) =>
    String(b.createdAt || b.created_at || '').localeCompare(String(a.createdAt || a.created_at || '')));
  if (!requests.length) {
    el.innerHTML = '<div class="empty-state"><span class="empty-icon">🌱</span><strong>No spawn requests</strong><p>Queued, failed, and completed spawns will appear here.</p></div>';
    return;
  }
  const rows = requests.map((req) => {
    const status = String(req.status || 'queued').toLowerCase();
    const chipStatus = status === 'done' ? 'completed' : status;
    const detail = req.error || req.claimedByBridgeId || '';
    const created = req.createdAt || req.created_at || '';
    return `<tr>
      <td>${created ? esc(relTime(created)) + ' ago' : '—'}</td>
      <td><strong>${esc(req.agentId || req.agent_id || '—')}</strong>${req.role ? `<br><span class="subtle">${esc(req.role)}</span>` : ''}</td>
      <td class="clip">${esc(req.environmentId || req.environment_id || '—')}</td>
      <td>${esc(req.runtime || '—')}</td>
      <td>${renderStatusChip(chipStatus, { label: status, why: `Spawn request status: ${status}.` })}</td>
      <td class="clip">${esc(req.workspace || '—')}</td>
      <td class="clip">${esc(detail)}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div class="table-wrap"><table class="spawn-requests-table"><thead><tr>
      <th>Requested</th><th>Agent</th><th>Environment</th><th>Runtime</th><th>Status</th><th>Workspace</th><th>Bridge / error</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------------------------------
// The environment SUMMARY tile and the workspace-roots editor, added in a later v0.5.4 slice.
//
// They join this module rather than getting one of their own: same subject, same page, and the roots
// editor renders into the inspector the panels above drive. Joining an existing owner over creating a new
// module is the standing rule here, and two declarations is the case it exists for.
//
// They were NOT extractable when this module was first written: `renderEnvironmentSummary` reads `metric`,
// which was still in app.js and only got an owner (`summary-tiles.mjs`) a slice later. Re-surveying after
// every slice is what surfaced them — the blocked set shrinks as owners appear.

export function renderEnvironmentSummary() {
  const target = byId('environment-summary');
  if (!target) return;
  const online = state.environments.filter((env) => resolveStatus(env.status).kind === 'online').length;
  const offline = state.environments.filter((env) => resolveStatus(env.status).kind === 'offline').length;
  const runtimeKinds = new Set();
  state.environments.forEach((env) => environmentRuntimes(env).forEach((runtime) => runtimeKinds.add(runtime.runtime)));
  target.innerHTML = [
    metric('Environments', state.environments.length, state.environments.length ? 'ok' : 'neutral'),
    metric('Online bridges', online, online ? 'ok' : 'neutral'),
    metric('Offline', offline, offline ? 'bad' : 'neutral'),
    metric('Runtime types', runtimeKinds.size, runtimeKinds.size ? 'working' : 'neutral'),
  ].join('');
}
export function openEnvironmentRootsEditor(environmentId) {
  const env = state.environments.find((e) => String(e.id) === String(environmentId)) || { id: environmentId };
  const roots = environmentRoots(env);
  const manualRoots = !!(env.metadata && (env.metadata.manualRoots || env.metadata.manual_roots));
  const overrideBadge = manualRoots
    ? '<span class="mb mb-warn" title="Roots were set from the dashboard and override what the bridge advertises">dashboard override active</span>'
    : '<span class="subtle">using bridge-advertised roots</span>';
  const startCmd = environmentStartCommand(env);
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>Workspace roots — ${esc(env.label || environmentId)}</strong></div>
      <div class="env-roots-state">${overrideBadge}</div>
      <label class="settings-label">Roots (one per line)
        <textarea id="env-edit-roots" rows="6" spellcheck="false" placeholder="C:/work&#10;C:/projects">${esc(roots.join('\n'))}</textarea>
      </label>
      <p class="subtle">Agents spawned in this environment must use a cwd under one of these roots. Leave non-empty; use “Reset to bridge roots” to restore the advertised set.</p>
      <label class="settings-label">Start command <span class="subtle">(run on the host to bring this bridge back)</span>
        <textarea id="env-start-cmd" rows="2" spellcheck="false" readonly>${esc(startCmd)}</textarea>
      </label>
      <div class="agent-drawer-actions">
        <button class="primary" data-env-roots-submit="${esc(environmentId)}">Save roots</button>
        <button class="ghost" data-env-roots-reset="${esc(environmentId)}">Reset to bridge roots</button>
        <button class="ghost" data-copy-text="${esc(startCmd)}">Copy start command</button>
      </div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'env-roots', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
  setTimeout(() => byId('env-edit-roots')?.focus(), 30);
}

// --- ACTIONS ------------------------------------------------------------------------------------
//
// The four things an operator can DO to an environment, moved here in v0.5.4 to sit with the panels
// that render them. `createSpawnRequest` is the one that matters most: it is how a new managed worker
// comes into existence, and every field it reads is a free-text input, so what it sends is what the
// operator typed — including the empty string.
//
// Three injected names, each of which reaches `refresh`.

let closeInspector = () => {};
let inspect = () => {};
let refresh = async () => {};
let refreshSoon = () => {};

/** Supply the app.js-side dependencies for the actions above. Throws on a partial bag. */
export function initEnvironmentActions(deps) {
  const REQUIRED = ['closeInspector', 'inspect', 'refresh', 'refreshSoon'];
  const missing = REQUIRED.filter((k) => typeof deps?.[k] !== 'function');
  if (missing.length) throw new TypeError(`initEnvironmentActions requires ${missing.join(', ')}`);
  ({ closeInspector, inspect, refresh, refreshSoon } = deps);
}


export async function controlEnvironment(environmentId, action) {
  if ((action === 'stop' || action === 'forget') && !await uiConfirm(`${action === 'stop' ? 'Stop the bridge process' : 'Forget this environment'} "${environmentId}"?`)) return;
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/control`, { method: 'POST', body: JSON.stringify({ action, requestedBy: 'dashboard' }) });
    toast(`Environment ${action} requested`, 'ok');
    refreshSoon();
  } catch (err) { toast(`Environment ${action} failed: ${err?.message || err}`, 'error'); }
}

export async function submitEnvironmentRoots(environmentId) {
  const text = byId('env-edit-roots')?.value || '';
  const roots = text.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
  if (!roots.length) { toast('At least one root is required. Use “Reset to bridge roots” to restore advertised roots.', 'warn'); return; }
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/roots`, { method: 'PATCH', body: JSON.stringify({ roots, requestedBy: 'dashboard' }) });
    toast('Workspace roots updated', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Root update failed: ${err?.message || err}`, 'error'); }
}

export async function resetEnvironmentRoots(environmentId) {
  if (!await uiConfirm(`Reset "${environmentId}" to the roots advertised by its bridge process?`)) return;
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/roots`, { method: 'PATCH', body: JSON.stringify({ resetToBridgeAdvertised: true, requestedBy: 'dashboard' }) });
    toast('Workspace roots reset to bridge-advertised', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Root reset failed: ${err?.message || err}`, 'error'); }
}

export async function createSpawnRequest() {
  const environmentId = byId('env-spawn-environment')?.value || '';
  const runtime = byId('env-spawn-runtime')?.value || '';
  const agentId = byId('env-spawn-agent-id')?.value.trim() || '';
  const role = byId('env-spawn-role')?.value || 'coder';
  const workspace = byId('env-spawn-workspace')?.value.trim() || '';
  const initialMessage = byId('env-spawn-prompt')?.value.trim() || '';
  if (!environmentId || !runtime || !agentId || !workspace) {
    toast('Need environment, runtime, agent ID, and workspace.', 'warn');
    return;
  }
  const result = await api('/spawn-requests', {
    method: 'POST',
    body: JSON.stringify({
      createdBy: 'dashboard',
      environmentId,
      agentId,
      role,
      runtime,
      workspace,
      initialMessage,
      subject: initialMessage ? `Spawn ${agentId}` : '',
      mode: 'managed-warm',
    }),
  });
  byId('env-spawn-agent-id').value = '';
  byId('env-spawn-prompt').value = '';
  inspect('spawn-request', result.spawnRequest || result);
  await refresh();
}
