// Agent and session lifecycle actions: the buttons that change what an agent IS or stop what it is
// doing. Mode switch, edit, continue/compact, remove, stop-worker, delete-session, the sticky-identity
// resolution, and the session controls (single and bulk).
//
// Every one of these has an effect on a live process, and none of them was reachable by a test while
// they lived in app.js. The riskiest are the two that end running work — `stopAgentWorker` and
// `requestSessionControl('stop')` — and the two that are irreversible — `removeAgent` and
// `deleteSessionById`. Their confirmations are behaviour, not decoration, and are asserted as such.
//
// The nine injected names are app.js's render orchestrator and its neighbours; each reaches `refresh`,
// so importing any would pull the whole render web in here. Init is explicit, like realtime-socket.mjs
// and run-inspector.mjs.

import { openAgentDrawer } from './agent-drawer.mjs';
import { api, apiBase } from './api-client.mjs';
import { sessionAgentId, sessionEnvironmentId, sessionId, sessionRuntime } from './record-fields.mjs';
import { renderSessionRail, selectedSessionIds } from './session-rail.mjs';
import { state } from './state.mjs';
import { byId, toast, uiConfirm, uiPrompt } from './ui.js';

let chatController = { close() {}, render() {} };
let closeInspector = () => {};
let inspect = () => {};
let markConversationRead = async () => {};
let refresh = async () => {};
let refreshSoon = () => {};
let renderSessionWorkspace = () => {};
let setPage = () => {};

/** Supply the app.js-side dependencies. Throws on a partial bag rather than accepting no-ops. */
export function initAgentSessionActions(deps) {
  const REQUIRED = ['chatController', 'closeInspector', 'inspect', 'markConversationRead', 'refresh',
    'refreshSoon', 'renderSessionWorkspace', 'setPage'];
  const missing = REQUIRED.filter((k) => deps == null || deps[k] == null);
  if (missing.length) throw new TypeError(`initAgentSessionActions requires ${missing.join(', ')}`);
  ({ chatController, closeInspector, inspect, markConversationRead, refresh, refreshSoon,
    renderSessionWorkspace, setPage } = deps);
}


export async function switchAgentSessionMode(agentId, targetMode, { force = false } = {}) {
  if (!agentId || !targetMode) return null;
  const url = `${apiBase}/agents/${encodeURIComponent(agentId)}/session-mode`;
  let res;
  try {
    res = await fetch(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: targetMode, force, requestedBy: 'dashboard' }),
    });
  } catch (err) {
    inspect('Mode switch error', { agentId, targetMode, error: String(err?.message || err) });
    return null;
  }
  let body = null;
  try { body = await res.json(); } catch {}
  if (!res.ok) {
    // I10: an active run blocks the switch (409). Offer to force it, matching the old dashboard.
    if (res.status === 409 && !force) {
      const detail = body?.detail || body?.error || 'An active run is blocking the switch.';
      if (await uiConfirm(`${detail}\n\nForce the switch to ${targetMode} anyway?`)) {
        return switchAgentSessionMode(agentId, targetMode, { force: true });
      }
      return null;
    }
    toast(`Mode switch failed: ${body?.detail || body?.error || res.status}`, 'error');
    inspect('Mode switch failed', { agentId, targetMode, status: res.status, body });
    return null;
  }
  const updatedMode = String(body?.mode || targetMode);
  const existingAgent = state.agents.find((agent) => String(agent.id || '') === String(agentId));
  if (existingAgent && body?.agent) Object.assign(existingAgent, body.agent);
  else if (existingAgent) existingAgent.sessionMode = updatedMode;
  // The AGENT carries the optimistic paint. A loop here used to also set `session.sessionMode` on
  // every row of that agent, and nothing reads it: the drawer and the directory read
  // `agent.sessionMode || session.mode`, the console reads `agent?.sessionMode || session?.ownerMode`,
  // and /sessions emits no `sessionMode` at all. Writing `session.mode` instead would be wrong
  // rather than better -- that field holds 'managed-warm', a different vocabulary from 'managed'.
  renderSessionRail();
  renderSessionWorkspace();
  chatController.render();
  toast(`Switched ${agentId} to ${updatedMode}`, 'ok');
  refreshSoon();
  return body;
}

export async function submitAgentEdit(agentId) {
  const agent = state.agents.find((a) => a.id === agentId) || {};
  const newId = byId('edit-agent-id')?.value.trim() || agentId;
  const desc = byId('edit-agent-desc')?.value ?? '';
  const handle = byId('edit-agent-handle')?.value.trim() ?? '';
  const willRename = !!(newId && newId !== agentId);
  // Confirm the rename UP FRONT — confirming after the other edits already fired left those
  // writes applied with no toast/refresh when the operator cancelled the rename prompt.
  if (willRename && !await uiConfirm(`Rename "${agentId}" → "${newId}"? Chats/sessions/records move to the new id.`)) return;
  try {
    if (desc !== (agent.description || '')) {
      await api(`/agents/${encodeURIComponent(agentId)}/description`, { method: 'PATCH', body: JSON.stringify({ description: desc }) });
    }
    if (handle !== String(agent.sessionHandle || '')) {
      await api(`/agents/${encodeURIComponent(agentId)}/session-handle`, { method: 'PATCH', body: JSON.stringify({ sessionHandle: handle }) });
    }
    const envId = byId('edit-agent-env')?.value.trim() || '';
    if (envId) {
      const runtime = byId('edit-agent-runtime')?.value.trim() || '';
      const workspace = byId('edit-agent-workspace')?.value.trim() || '';
      const body = { environmentId: envId };
      if (runtime) body.runtime = runtime;
      if (workspace) body.workspace = workspace;
      await api(`/agents/${encodeURIComponent(agentId)}/environment`, { method: 'POST', body: JSON.stringify(body) });
    }
    if (willRename) {
      await api(`/agents/${encodeURIComponent(agentId)}/rename`, { method: 'POST', body: JSON.stringify({ newAgentId: newId }) });
    }
    toast('Agent updated', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Edit failed: ${err?.message || err}`, 'error'); }
}

// Sticky session identity (governance): resolve a `session-changed` agent by
// confirming the new (pending) id or keeping the pinned handle. Both endpoints
// clear the pending id and exit the session-changed state.
export async function resolveAgentSession(agentId, mode) {
  const path = mode === 'confirm' ? 'session/confirm' : 'session/keep';
  const label = mode === 'confirm' ? 'Confirm new session id' : 'Keep pinned handle';
  try {
    const res = await api(`/agents/${encodeURIComponent(agentId)}/${path}`, { method: 'POST', body: JSON.stringify({ requestedBy: 'dashboard' }) });
    // `keep` surfaces the runtime resume command so the operator can re-attach the
    // agent onto the pinned id. Show it in a prompt-style dialog for easy copy.
    if (mode === 'keep' && res && res.resumeCommand) {
      await uiPrompt('Re-attach the agent to its pinned session with this command:', { defaultValue: res.resumeCommand, confirmLabel: 'Done' });
    } else {
      toast(`${label}: done`, 'ok');
    }
    await refresh();
    openAgentDrawer(agentId);
  } catch (err) { toast(`${label} failed: ${err?.message || err}`, 'error'); }
}

export async function submitContinue(sid, splitIdentity) {
  const target = state.sessions.find((s) => String(sessionId(s)) === String(sid));
  if (!target) { toast('Session not found', 'error'); return; }
  const v = (id) => byId(id)?.value?.trim() || '';
  const sourceAgent = sessionAgentId(target) || '';
  const newAgentId = splitIdentity ? v('cont-agent-id') : (v('cont-agent-id') || sourceAgent);
  if (!newAgentId) { toast('Agent ID is required', 'error'); return; }
  // SAY WHICH FIELD IS MISSING, HERE, rather than posting a value the server has to reject.
  // These two fell back to `sessionEnvironmentId`/`sessionRuntime`, which used to answer the display
  // sentinels 'unassigned' and 'runtime' — so a session with no binding posted
  // `environmentId: "unassigned"` and the operator was told `Environment "unassigned" not found`,
  // naming an environment that has never existed anywhere. Empty is now empty, and the form says so.
  const environmentId = v('cont-env') || sessionEnvironmentId(target);
  if (!environmentId) { toast('Environment is required — this session has no binding, so type one', 'error'); return; }
  const runtime = v('cont-runtime') || sessionRuntime(target);
  if (!runtime) { toast('Runtime is required — this session does not name one, so type one', 'error'); return; }
  try {
    await api('/spawn-requests', {
      method: 'POST',
      body: JSON.stringify({
        createdBy: 'dashboard', environmentId,
        agentId: newAgentId, role: v('cont-role') || 'coder', runtime,
        workspace: v('cont-workspace') || target.workspace || target.cwd, initialMessage: v('cont-packet'),
        subject: splitIdentity ? `Continue as from ${sourceAgent}` : `Handoff compact from ${sourceAgent}`,
        mode: 'managed-warm', resumePolicy: 'fresh_context',
        metadata: { continuedFromSessionId: sid, continuedFromAgentId: sourceAgent, compactMode: 'handoff', sameAgentId: newAgentId === sourceAgent, splitIdentity },
      }),
    });
    toast(splitIdentity ? `Continue-as queued for ${newAgentId}` : `Compact queued for ${newAgentId}`, 'ok');
    closeInspector();
    refreshSoon();
    setPage('environments');
  } catch (err) { toast(`Continue failed: ${err?.message || err}`, 'error'); }
}

export async function removeAgent(agentId) {
  if (!agentId) return;
  if (!await uiConfirm(`Remove agent "${agentId}"? This tombstones the identity.`)) return;
  try {
    await api(`/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
    toast(`Removed ${agentId}`, 'ok');
    closeInspector();
    refreshSoon();
  } catch (err) { toast(`Remove failed: ${err?.message || err}`, 'error'); }
}

// Kill a live worker from the details drawer, keyed on the AGENT rather than a session row
// (2026-07-26). /agents/{id}/stop-worker is the authoritative teardown: it ends the live
// agent_sessions rows, terminal bindings, virtual-terminal pointer and turn_busy pulse, and the
// agent reports `available`. Identity, history and the resume handle survive, so this is "stop",
// not "remove" — the agent can be started again from the same drawer.
// Confirmed because it kills real running work.
export async function stopAgentWorker(agentId) {
  if (!agentId) return;
  if (!await uiConfirm(
    `Stop ${agentId}'s live worker?\n\n`
    + 'Any turn it is running is lost. Its identity, history and resume handle are kept, '
    + 'so you can start it again.',
  )) return;
  try {
    await api(`/agents/${encodeURIComponent(agentId)}/stop-worker`, {
      method: 'POST',
      body: JSON.stringify({ requestedBy: 'dashboard' }),
    });
    toast(`Stopped ${agentId}'s worker`, 'ok');
    // AWAIT the refresh before re-rendering (review 2026-07-26). Rendering straight after the POST
    // painted the drawer from the PRE-stop `state.agents`, so it still showed the old status and a
    // live "Stop worker" button for a worker that was already gone. Pull fresh state first, then
    // re-render, so the drawer reflects the real post-stop status.
    try { await refresh(); } catch { /* keep the drawer usable even if that poll failed */ }
    openAgentDrawer(agentId);
  } catch (err) { toast(`Stop failed: ${err?.message || err}`, 'error'); }
}

export async function deleteSessionById(sid) {
  if (!sid) return;
  if (!await uiConfirm('Delete this session record?')) return;
  try {
    await api(`/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' });
    toast('Session deleted', 'ok');
    closeInspector();
    refreshSoon();
  } catch (err) { toast(`Delete failed: ${err?.message || err}`, 'error'); }
}

export async function requestSessionControl(sessionId, action, confirmAction = true, refreshAfter = true) {
  const labels = {
    stop: 'stop this session',
    restart: 'restart this session using its saved backing',
    recreate: 'RESET this session with a fresh context (the current native session is discarded)',
  };
  if (!sessionId || !action) return;
  if (confirmAction && !await uiConfirm(`Really ${labels[action] || action}?`)) return;
  try {
    await api(`/sessions/${encodeURIComponent(sessionId)}/control`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        from_agent: 'dashboard',
        // NO `body`. It is not a note -- the route stores it as the spawn request's
        // `initial_message`, and `_hand_settled_spawn_to_dispatch` turns a non-empty one into a
        // real `type=request` MESSAGE plus a dispatch run addressed to the agent that just came
        // up. So a receipt reading "Session restart requested from Dashboard Next." arrived at
        // the freshly-restarted agent as an instruction owing a reply, and the agent did the
        // obvious thing: it called comms_restart on itself.
        //
        // MEASURED on the operator's fleet: all 21 self-issued spawn requests are preceded, 45
        // to 75 seconds earlier, by exactly one of these -- a dashboard `Restart <agent>`
        // message of type=request. That is the whole of "agents exited even though I never
        // stopped them": the operator restarted one agent and the loop kept going.
        //
        // The service is not wrong. `initial_message` is for a BRIEF -- work the new worker is
        // being started to do -- and the message it becomes exists so the agent's inbox is not
        // empty and it has an id to thread a reply to. A control has no brief; sending one was
        // the mistake. `comms_restart` on the bridge side already sends no body.
      }),
    });
    if (refreshAfter) await refresh();
  // NAMES THE SESSION. This handler is the only one that ever runs -- it swallows, so the bulk
  // caller's own catch below can never fire, and the message that named the failing id lived
  // there. Over one session the operator knows which; over twenty they had no way to tell.
  } catch (err) { toast(`Session ${sessionId} ${action} failed: ${err?.message || err}`, 'error'); }
}

export async function requestBulkSessionControl(action) {
  const ids = selectedSessionIds();
  if (!ids.length || !action) return;
  if (!await uiConfirm(`Really ${action} ${ids.length} selected session${ids.length === 1 ? '' : 's'}?`)) return;
  for (const id of ids) {
    if (action === 'delete') {
      try { await api(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch (err) { toast(`Delete ${id} failed: ${err?.message || err}`, 'error'); }
    } else {
      // Isolation comes from requestSessionControl swallowing its own error, not from this catch:
// it cannot throw, so the loop continues either way and the catch below never runs. Kept as
// defence in depth for the day that changes; the failing id is named by the callee now.
      // (and skip the trailing clear()/refresh()).
      try { await requestSessionControl(id, action, false, false); } catch (_) { /* the callee reported it, and names the session */ }
    }
  }
  state.selectedSessionIds.clear();
  await refresh();
}

// Deep-link to the Chat page for a given agent (used by "Message in Chat" + message threads).
export function openAgentChat(agentId) {
  if (!agentId || agentId === 'dashboard') { setPage('chat'); return; }
  setPage('chat');
  // "Message in Chat" must land on the messenger, not follow a stale open analytics panel.
  state.chat.analytics = { agent: '', data: null };
  chatController.open(`dm:${agentId}`);
  if (!state.chat.peek) markConversationRead(agentId, { quiet: true }); // respect Peek mode on deep-link opens too
  byId('chat-composer-body')?.focus();
}
