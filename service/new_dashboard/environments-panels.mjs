// The Environments page: the environment list, the spawn-request queue, and the spawn form's options.
//
// One subject, three renderers, extracted from app.js in v0.5.4 as a measured closure needing only sibling
// leaf modules imported downward.
//
// The main table reads new-agent lifecycle receipts. Legacy queue records have a separate, on-demand history.

export function renderEnvironmentSpawnOptions(selectedEnvId = byId('env-spawn-environment')?.value || '') {
  const envSelect = byId('env-spawn-environment');
  const runtimeSelect = byId('env-spawn-runtime');
  if (!envSelect || !runtimeSelect) return;
  // Don't rebuild the spawn dropdowns while the operator is interacting with the form — the 15s
  // poll would otherwise reset an open/selected dropdown (deep-audit C minor).
  if (byId('environment-spawn-form')?.contains(document.activeElement)) return;
  const currentEnv = state.environments.some((env) => String(env.id) === selectedEnvId)
    ? selectedEnvId
    // PREFER A HOST THAT CAN CLAIM. `online` alone preselected machines where nothing could
    // run the work, and the operator found out from a refusal rather than from this form.
    : String(state.environments.find((env) => resolveStatus(env.status).kind === 'online' && spawnClaim(env).canSpawn)?.id
      || state.environments.find((env) => resolveStatus(env.status).kind === 'online')?.id
      || state.environments[0]?.id || '');
  envSelect.innerHTML = '<option value="">Environment</option>' + state.environments.map((env) => {
    const claim = spawnClaim(env);
    // The option stays SELECTABLE when it cannot claim. Disabling it would hide the reason, and an
    // operator who has just restarted a claimer needs to be able to try the host they expect.
    const note = claim.canSpawn ? '' : ' — cannot spawn';
    return `<option value="${esc(env.id)}"${String(env.id) === currentEnv ? ' selected' : ''}>${esc(env.label || env.id)} (${esc(resolveStatus(env.status).label)})${note}</option>`;
  }).join('');
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
import { environmentRoots, environmentRuntimes } from './record-fields.mjs';
import { state, inspectorOpening } from './state.mjs';
import { renderStatusChip, resolveStatus, spawnClaim, statusWhyContext } from './status.js';
import { metric } from './summary-tiles.mjs';
import { byId, toast, uiConfirm } from './ui.js';
import { esc, relTime, relTimeHtml } from './util.js';
import { launchRootsNote, rootChip } from './env-launch-roots.mjs';
import { loadSpawnHistory, renderLifecycleSpawnStatus } from './spawn-history.mjs';

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
  // Carries its own instant (see `relTimeHtml`) so the ticker refreshes the number in place.
  const seen = relTimeHtml(env.lastSeen);
  return seen ? ` · last seen ${seen} ago` : '';
}

/**
 * A badge when this environment's bridge is running a DIFFERENT build than the service.
 *
 * WHY THIS EXISTS. The operator restarted aify-env twice trying to make an empty AGENT column
 * fill in. It never could: the column is filled from a label the aify-comms BRIDGE sends at spawn
 * time, and the running bridge predated that code by two commits -- build 579dd546 against a
 * service built from 45045505, 231 commits apart. Nothing on any screen said so. `aify-comms
 * doctor` has said it all along under `bridge-current`, but that is a command you have to know to
 * run, and the environment card is where somebody looks when an environment misbehaves.
 *
 * A MISMATCH IS NOT AN ERROR. The service is a container build and the bridge is host code
 * installed separately, so they differ routinely between a rebuild and a wrapper relaunch. The
 * badge says what is true and what to do, and stays out of the status chip, which answers a
 * different question.
 *
 * ABSENCE IS NOT A MISMATCH. Either side missing renders nothing: a bridge too old to report its
 * build, or a `/version` that has not answered yet, is no evidence at all -- and a badge that
 * appeared on every load until the first poll is one nobody would read twice.
 */
/**
 * A badge when this host has stopped pushing its agent definitions (P0 C11). Its definitions keep
 * governing as last pushed, so the operator must see that nothing is refreshing them. The sentence is
 * the service's (`definitions.notice`), printed as given; empty while the host pushes.
 */
export function staleDefinitionsBadge(env) {
  const notice = String((env && env.definitions && env.definitions.notice) || '').trim();
  if (!notice) return '';
  const title = 'These agent definitions still govern as last pushed. aify-env on that machine pushes '
    + 'every minute while it runs: check it is running and current (aify-env doctor there).';
  return `<span class="mb mb-warn" title="${esc(title)}">${esc(notice)}</span>`;
}

/**
 * A badge when this environment is running code its own disk has moved past.
 *
 * WHY THIS EXISTS. `bridge-current` used to answer a version of this and its SUBJECT MOVED OUT FROM
 * UNDER IT: it compared each bridge's `bridgeBuild` against the repo, and v0.6.1 retired the tier
 * that reported one. The useful question outlived the check -- an environment tier running code
 * older than its own checkout means every fix since is inert -- and the operator will not run a
 * command to find out. So the tier advertises both identities and this shows the answer where they
 * already look.
 *
 * READ, NOT DERIVED. `codeCurrency` is computed service-side on the environment row, the same way
 * `spawnClaim` is. The doctor and `comms_envs` each grew their own copy of the claim rule and each
 * got it wrong once; a comparison reimplemented in a browser bundle is that again.
 *
 * SILENT UNLESS STALE. `current` needs no badge and `unknown` must not draw one -- an advertiser too
 * old to report the pair is no evidence either way, and a badge that appears on every host until
 * everything is upgraded is one nobody reads twice. Absence of a badge here is not a claim that a
 * host is current; the doctor row says which of the two it is.
 *
 * BOTH IDENTITIES IN THE TITLE, because the remedy is a restart and restarting an environment tier
 * reaps the managed workers it is running. Advice that costs somebody their agents has to be
 * arguable, and an operator who doubts the badge needs the two numbers to check it with.
 */
export function staleCodeBadge(env) {
  const currency = env && env.codeCurrency;
  if (!currency || currency.state !== 'stale') return '';
  const running = String(currency.running || '').trim();
  const onDisk = String(currency.onDisk || '').trim();
  if (!running || !onDisk) return '';
  const title = `This host is running build ${running}, but the code on its disk is ${onDisk}. It `
    + `loaded its files before they changed, so anything fixed since is NOT live here. Restarting `
    + `the environment picks it up -- and reaps the managed workers it is currently running, so do `
    + `it when losing them is acceptable.`;
  return `<span class="mb mb-warn" title="${esc(title)}">running ${esc(running)} \u2260 disk ${esc(onDisk)}</span>`;
}

/**
 * Why this environment cannot open a terminal, when it cannot.
 *
 * THIS IS THE OTHER HALF OF A FIX, and without it that fix would have been a trade. The bridge used
 * to advertise `terminal: true` from its own node-pty, which since v0.6 Phase 8 is not the tier that
 * opens anything -- so with aify-env down, twenty managed agents read `available` and every send to
 * them failed. Correcting that makes them read `offline`, which is true, and says NOTHING about why.
 * An operator would go hunting a delivery bug: the same wrong hunt, one tier over.
 *
 * SHOWN ONLY WHEN THE ANSWER IS NO. A card that explains why everything is fine is noise, and the
 * reason is most worth reading at the moment the card stops offering to spawn.
 *
 * ABSENT RENDERS NOTHING -- a bridge too old to send a reason has not given one, and inventing
 * `unknown` here would put a word in its mouth.
 */
export function terminalReasonNote(env) {
  if (env?.terminal !== false) return '';
  const reason = String(env?.metadata?.terminalReason || '').trim();
  if (!reason) return '';
  return `<p class="subtle env-terminal-reason">No terminal: ${esc(reason)}</p>`;
}

export function renderRuntime() {
  byId('environment-list').innerHTML = state.environments.map((env) => `
    <article class="runtime-card" data-kind="environment" data-id="${esc(env.id)}">
      <div class="item-title"><strong>${esc(env.label || env.id)}</strong>${renderStatusChip(env.status, statusWhyContext('environment', env, env.status))}</div>
      <p class="preview">${esc(env.kind || env.os || '')} · ${esc(env.machineId || '')}${offlineAge(env)}${staleCodeBadge(env)}${staleDefinitionsBadge(env)}</p>
      ${terminalReasonNote(env)}
      <div class="env-runtime-list">
        ${environmentRuntimes(env).map((runtime) => `<span class="env-runtime-pill${runtime.available === false ? ' unavailable' : ''}">${esc(runtime.runtime)}${runtime.available === false ? ' (unavailable)' : ''}</span>`).join('') || '<span class="env-runtime-pill unavailable">no runtimes</span>'}
      </div>
      <div class="env-root-list">
        ${(() => { const roots = environmentRoots(env); const shown = roots.slice(0, 4).map((root) => rootChip(env, root)).join(''); const more = roots.length > 4 ? `<span class="subtle">+${roots.length - 4} more</span>` : ''; return (shown + more) || '<span class="subtle">No workspace roots advertised</span>'; })()}
      </div>
      ${launchRootsNote(env)}
      <div class="contract-actions">
        ${resolveStatus(env.status).kind === 'offline' ? '' : (() => {
          const claim = spawnClaim(env);
          // THREE ANSWERS, NOT TWO. `unproven` was returned by `spawnClaim` and read by nobody until
          // 2026-09-04 (external review, Round 8 M8) -- so a row this page CANNOT judge showed the
          // same confident button as one it had checked. A field nothing reads changes nothing, which
          // is this repo's own rule from the opposite end.
          //
          // The fail-open stays and its reasoning is unchanged: the service resolves an unstamped row
          // against `bridge_instances`, which no listing queries, so the page genuinely cannot know
          // and greying the button would take the feature from environments that work. What is fixed
          // is the silence.
          const title = claim.canSpawn && claim.unproven
            ? 'This environment carries no claim stamp, so whether a spawn is accepted cannot be told '
              + 'from here. Try it, or ask `aify-comms doctor` on that host.'
            : claim.canSpawn
              ? 'Open the spawn form prefilled for this environment'
              : `A spawn here would be refused: ${claim.why}. Start a claimer on that host.`;
          return `<button class="ghost${claim.canSpawn ? '' : ' danger'}" data-env-spawn="${esc(env.id)}" title="${esc(title)}">Spawn here…${claim.canSpawn ? '' : ' (no claimer)'}</button>`;
        })()}
        <button class="ghost" data-env-roots="${esc(env.id)}" title="Edit service workspace roots, not aify-env launch roots">Edit roots…</button>
        ${resolveStatus(env.status).kind === 'offline'
          ? `<button class="ghost danger" data-env-control="forget" data-env-id="${esc(env.id)}" title="Hide this offline environment (identities/chats/records remain)">Forget</button>`
          // No "Stop" for a live host: that control was drained by the aify-comms environment bridge
          // v0.6.2 deleted, and aify-env does not claim it, so the stop it queued was never acted on.
          : ''}
      </div>
    </article>`).join('') || '<div class="empty-state"><span class="empty-icon">🔌</span><strong>No environments connected</strong><p>Start <code>aify-env</code> on a host to see it here; <code>aify-env doctor</code> there says what it can see.</p></div>';
}
/**
 * Fetch the spawn requests this page renders, and draw them.
 *
 * Opening the page asks for the current rows, without waiting for a refresh or a change event.
 * On failure the panel names the failed read and keeps the last good rows.
 */
export async function loadSpawnRequests() {
  try { await loadSpawnHistory(); } catch (_) { /* the panel names the failed read */ }
  renderSpawnRequests();
}

function spawnTime(timestamp) {
  return timestamp ? `<time datetime="${esc(timestamp)}" title="${esc(timestamp)}">${relTimeHtml(timestamp)}</time>` : '—';
}

export function renderSpawnRequests() {
  const el = byId('spawn-requests-list');
  if (!el) return;
  const { spawnRequests: requests, spawnRequestsStatus: status } = state;
  const unavailable = status === 'unavailable'
    ? `<div class="mb mb-warn">Spawn history unavailable.${requests.length ? ' Showing the last successful read.' : ''}</div>`
    : '';
  if (!requests.length) {
    el.innerHTML = unavailable || `<p class="subtle">${status === 'ready'
      ? 'No lifecycle spawn requests.' : 'Loading spawn history…'}</p>`;
    return;
  }
  const rows = requests.map((req) => `<tr>
    <td>${spawnTime(req.createdAt)}</td><td>${spawnTime(req.finishedAt)}</td>
    <td><strong>${esc(req.agentId)}</strong></td><td class="clip">${esc(req.machineId || '—')}</td>
    <td>${esc(req.action)}</td><td>${renderLifecycleSpawnStatus(req.status, req.outcome)}</td>
    <td class="clip spawn-detail" title="${esc(req.outcome || '')}">${esc(req.outcome || '—')}</td>
  </tr>`).join('');
  el.innerHTML = `${unavailable}<div class="table-wrap"><table class="spawn-requests-table"><thead><tr>
    <th>Requested</th><th>Finished</th><th>Agent</th><th>Machine</th><th>Action</th><th>Status</th><th>Outcome</th>
  </tr></thead><tbody>${rows}</tbody></table></div>`;
}

export async function loadLegacySpawnRequests() {
  const el = byId('legacy-spawn-requests-list');
  if (!el) return;
  try {
    const result = await api('/spawn-requests?limit=200');
    if (!Array.isArray(result?.spawnRequests)) throw new Error('Legacy spawn history returned no list');
    renderLegacySpawnRequests(result.spawnRequests, result.truncated);
  } catch (_) {
    el.innerHTML = '<p class="mb mb-warn">Legacy spawn history unavailable.</p>';
  }
}

export function renderLegacySpawnRequests(records, truncated = false) {
  const el = byId('legacy-spawn-requests-list');
  if (!el) return;
  const requests = [...records].sort((a, b) =>
    String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
  if (!requests.length) {
    // THE STATES THIS TABLE CAN ACTUALLY SHOW. It promised "completed" spawns, and a spawn
    // request is never completed: the five statuses the service writes are queued, claimed,
    // running, failed and cancelled. An empty state that names a state the system has never had
    // tells the operator to wait for something that is not coming.
    el.innerHTML = '<div class="empty-state"><span class="empty-icon">🌱</span><strong>No spawn requests</strong><p>Queued, claimed, running, failed and cancelled spawns will appear here.</p></div>';
    return;
  }
  const rows = requests.map((req) => {
    // NO `done` MAPPING. It read `status === 'done' ? 'completed' : status`, and neither value is
    // one a spawn request can hold, so the branch was unreachable and its target meaningless.
    const status = String(req.status || 'queued').toLowerCase();
    const detail = req.error || req.claimedByBridgeId || '';
    const created = req.createdAt || '';
    return `<tr>
      <td>${created ? esc(relTime(created)) + ' ago' : '—'}</td>
      <td><strong>${esc(req.agentId || '—')}</strong>${req.role ? `<br><span class="subtle">${esc(req.role)}</span>` : ''}</td>
      <td class="clip">${esc(req.environmentId || '—')}</td>
      <td>${esc(req.runtime || '—')}</td>
      <td>${renderStatusChip(status, { label: status, why: `Spawn request status: ${status}.` })}</td>
      <td class="clip">${esc(req.workspace || '—')}</td>
      <td class="clip spawn-detail" title="${esc(detail)}">${esc(detail)}</td>
    </tr>`;
  }).join('');
  // This is the existing legacy page, not a limit on lifecycle history.
  const capped = truncated
    ? `<div class="mb mb-warn">Showing the newest ${requests.length} spawn requests; older ones are not loaded.</div>`
    : '';
  el.innerHTML = `${capped}<div class="table-wrap"><table class="spawn-requests-table"><thead><tr>
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
  // TWO TILES, because they answer different questions and one of them was missing. "Online bridges"
  // counts hosts that are DESCRIBED; this counts hosts that can take a spawn. They read the same on a
  // healthy fleet and diverge exactly when an operator needs to know.
  const claimable = state.environments.filter((env) => spawnClaim(env).canSpawn && resolveStatus(env.status).kind === 'online').length;
  const runtimeKinds = new Set();
  state.environments.forEach((env) => environmentRuntimes(env).forEach((runtime) => runtimeKinds.add(runtime.runtime)));
  target.innerHTML = [
    metric('Environments', state.environments.length, state.environments.length ? 'ok' : 'neutral'),
    metric('Online bridges', online, online ? 'ok' : 'neutral'),
    metric('Can spawn', claimable, claimable ? 'ok' : 'bad'),
    metric('Offline', offline, offline ? 'bad' : 'neutral'),
    metric('Runtime types', runtimeKinds.size, runtimeKinds.size ? 'working' : 'neutral'),
  ].join('');
}

/**
 * What an operator runs on a host whose environment is missing or silent: the diagnostic, and only
 * that. It answers without changing anything.
 *
 * STARTING aify-env IS NOT IN THE PASTE. It was, after the doctor, and a bare `aify-env` starts the
 * host tier: on a host with an idle one running, the paste superseded it. Whether to start one is the
 * operator's decision, so the page says how in words and copies nothing that acts (0.7.1 T05). This
 * was `cd <root>` + `aify-comms <roots>` before that, which since v0.6.1 refuses with exit 2.
 */
const HOST_CHECK_COMMAND = 'aify-env doctor';

/**
 * The hint shown in an empty roots box: an EXAMPLE of the shape this host writes, never a place this
 * service claims anything is installed. A Windows-only hint was the whole prompt on a Linux environment.
 */
export function rootsPlaceholder(env) {
  const os = String(env?.os || env?.kind || '').toLowerCase();
  if (os.includes('win')) return 'C:/work\nC:/projects';        // example path
  if (os.includes('mac') || os.includes('darwin')) return '/Users/you/work\n/Users/you/projects';
  return '/srv/work\n/home/you/projects';
}

export function openEnvironmentRootsEditor(environmentId) {
  const env = state.environments.find((e) => String(e.id) === String(environmentId)) || { id: environmentId };
  const roots = environmentRoots(env);
  const manualRoots = !!(env.metadata && (env.metadata.manualRoots || env.metadata.manual_roots));
  const overrideBadge = manualRoots
    ? '<span class="mb mb-warn" title="Roots were set from the dashboard and override what the host advertises">dashboard override active</span>'
    : '<span class="subtle">using the roots the host advertises</span>';
  const checkCmd = HOST_CHECK_COMMAND;
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>Workspace roots — ${esc(env.label || environmentId)}</strong></div>
      <div class="env-roots-state">${overrideBadge}</div>
      <label class="settings-label">Roots (one per line)
        <textarea id="env-edit-roots" rows="6" spellcheck="false" placeholder="${esc(rootsPlaceholder(env))}">${esc(roots.join('\n'))}</textarea>
      </label>
      <p class="subtle">Saving here changes the service's workspace roots, not aify-env's launch roots. To add a launch root, run <code>aify-env roots add &lt;folder&gt;</code> on the host. Keep at least one workspace root, or reset to the advertised workspace list.</p>
      <label class="settings-label">Host check <span class="subtle">(run on the host: it says whether aify-env is running there and what it can see, and changes nothing)</span>
        <textarea id="env-check-cmd" rows="1" spellcheck="false" readonly>${esc(checkCmd)}</textarea>
      </label>
      <p class="subtle">Starting aify-env on that host is the operator's action: run <code>aify-env</code> there yourself if the check says none is running. One already running agents is shown rather than replaced.</p>
      <div class="agent-drawer-actions">
        <button class="primary" data-env-roots-submit="${esc(environmentId)}">Save roots</button>
        <button class="ghost" data-env-roots-reset="${esc(environmentId)}">Reset workspace roots</button>
        <button class="ghost" data-copy-text="${esc(checkCmd)}">Copy host check</button>
      </div>
    </div>`;
  state.inspector = inspectorOpening('env-roots', { runId: '' });
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
let refresh = async () => {};
let refreshSoon = () => {};

/** Supply the app.js-side dependencies for the actions above. Throws on a partial bag. */
export function initEnvironmentActions(deps) {
  const REQUIRED = ['closeInspector', 'refresh', 'refreshSoon'];
  const missing = REQUIRED.filter((k) => typeof deps?.[k] !== 'function');
  if (missing.length) throw new TypeError(`initEnvironmentActions requires ${missing.join(', ')}`);
  ({ closeInspector, refresh, refreshSoon } = deps);
}


export async function controlEnvironment(environmentId, action) {
  if (action === 'forget' && !await uiConfirm(`Forget this environment "${environmentId}"?`, { tone: 'danger' })) return;
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/control`, { method: 'POST', body: JSON.stringify({ action, requestedBy: 'dashboard' }) });
    toast(`Environment ${action} requested`, 'ok');
    refreshSoon();
  } catch (err) { toast(`Environment ${action} failed: ${err?.message || err}`, 'error'); }
}

export async function submitEnvironmentRoots(environmentId) {
  const text = byId('env-edit-roots')?.value || '';
  const roots = text.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
  if (!roots.length) { toast('At least one root is required. Use “Reset workspace roots” to restore the advertised workspace list.', 'warn'); return; }
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/roots`, { method: 'PATCH', body: JSON.stringify({ roots, requestedBy: 'dashboard' }) });
    toast('Workspace roots updated', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Root update failed: ${err?.message || err}`, 'error'); }
}

export async function resetEnvironmentRoots(environmentId) {
  if (!await uiConfirm(`Reset "${environmentId}" to the roots its host advertises?`)) return;
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/roots`, { method: 'PATCH', body: JSON.stringify({ resetToBridgeAdvertised: true, requestedBy: 'dashboard' }) });
    toast('Workspace roots reset to what the host advertises', 'ok');
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
  await api('/spawn-requests', {
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
  // D8: the host defines it first, and it is spawned once that definition is published. A sentence, not the
  // drawer taken over by the record's JSON.
  toast(`Asked its host to define ${agentId}; it starts once defined`, 'ok');
  await refresh();
}
