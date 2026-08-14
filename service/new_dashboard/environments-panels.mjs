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

import { environmentRoots, environmentRuntimes } from './record-fields.mjs';
import { state } from './state.mjs';
import { renderStatusChip, resolveStatus, statusWhyContext } from './status.js';
import { byId } from './ui.js';
import { esc, relTime } from './util.js';

export function renderRuntime() {
  byId('environment-list').innerHTML = state.environments.map((env) => `
    <article class="runtime-card" data-kind="environment" data-id="${esc(env.id)}">
      <div class="item-title"><strong>${esc(env.label || env.id)}</strong>${renderStatusChip(env.status, statusWhyContext('environment', env, env.status))}</div>
      <p class="preview">${esc(env.kind || env.os || '')} · ${esc(env.machineId || env.machine_id || '')}</p>
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
