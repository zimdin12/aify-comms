// The agent drawer: the right-hand panel that opens on an agent, and the selection sync that keeps it
// pointed at the right one.
//
// Extracted from app.js in v0.5.4 as a measured closure — three declarations that need nothing from app.js,
// only sibling leaf modules imported downward. That became possible once `state` and `byId` were given
// owners of their own; before that every render group in app.js read at least one name app.js itself
// declared, and a module extracted from app.js cannot import those back without the upward import this
// series forbids — which here would also be a cycle.
//
// NOT THE RUN INSPECTOR, deliberately. `renderRunInspector` looks like the same subject and is not part of
// this closure: it calls `evaluateFlowGates`, whose `flowGates` entries probe half of app.js
// (`connectRealtimeSocket`, `renderSessionWorkspace`, `createSpawnRequest`, …) through
// `typeof X === 'function'` checks. An earlier slice took that group on a measurement that walked only the
// call graph between functions and never read a `const`'s initializer; it was written, passed the
// byte-identical reconstruction proof, and had to be reverted. Measured correctly, `renderRunInspector`
// reaches 138 declarations and 2,532 lines. See docs/APP_JS_STATE_MODULE_PACKET.md, fifth correction.
//
// `sessionForAgent` comes along because this closure is what reaches it; app.js imports it back.
//
// Every declaration is byte-identical to the one that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Leading comments stayed behind in
// app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its comments
// could not round-trip through the proof.


import { continueCliDetails, resumeMachineNote } from './cli-resume.mjs';
import { sessionAgentId, sessionEnvironmentId, sessionId, sessionRuntime } from './record-fields.mjs';
import { state } from './state.mjs';
import { renderStatusChip, statusWhyContext } from './status.js';
import { byId } from './ui.js';
import { esc, relTimeHtml } from './util.js';
import { api } from './api-client.mjs';
import { AGENT_PROCESSES_ID, loadAgentProcesses } from './agent-processes.mjs';
import { AGENT_RUNS_ID, fillAgentRuns } from './agent-runs.mjs';
import { AGENT_SHARING_ID, fillSessionSharing } from './agent-session-sharing.mjs';

export function sessionForAgent(agentId) {
  return state.sessions.find((session) => sessionAgentId(session) === agentId) || null;
}
export function openAgentDrawer(agentId) {
  const id = String(agentId || '').trim();
  if (!id) return;
  const agent = state.agents.find((a) => a.id === id) || { id };
  const session = sessionForAgent(id);
  const env = session ? (state.environments.find((e) => String(e.id) === String(sessionEnvironmentId(session))) || null) : null;
  const sid = session ? sessionId(session) : '';
  const mode = String(agent.sessionMode || (session && session.mode) || 'resident').toLowerCase();
  const otherMode = mode === 'managed' ? 'resident' : 'managed';
  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value}</dd>`;
  // AGENT-LEVEL stop (2026-07-26, operator request: "a way to stop/kill an online agent").
  // Every other action here is gated on `sid` — a resolvable session row — so an agent whose
  // session is missing/unresolved offered NO way to stop it from the dashboard at all. This one
  // is keyed on the AGENT and hits /agents/{id}/stop-worker, the authoritative teardown: it ends
  // the live worker, terminal bindings and turn_busy pulse, reports `available`, and PRESERVES
  // registration + session_handle so the agent can be started again later.
  // Offered whenever the agent isn't already down, so it works for exactly the `online`/`working`
  // case the operator hit.
  const agentStatus = String(agent.status || '').trim().toLowerCase();
  const canStopWorker = !['offline', 'stopped', 'available'].includes(agentStatus);
  const actions = [
    canStopWorker
      ? `<button class="ghost danger" data-agent-stop-worker="${esc(id)}" title="Kill this agent's live worker. Identity, history and resume handle are kept — it can be started again.">Stop worker</button>`
      : '',
    sid ? `<button class="ghost" data-agent-control="restart" data-session="${esc(sid)}">Restart</button>` : '',
    sid ? `<button class="ghost" data-agent-control="recreate" data-session="${esc(sid)}" title="Restart with a FRESH context (discards native session)">Reset</button>` : '',
    sid ? `<button class="ghost danger" data-agent-control="stop" data-session="${esc(sid)}">Stop session</button>` : '',
    sid ? `<button class="ghost" data-agent-compact="${esc(sid)}">Compact</button>` : '',
    sid ? `<button class="ghost" data-agent-continue="${esc(sid)}">Continue as…</button>` : '',
    `<button class="ghost" data-agent-mode="${esc(otherMode)}" data-agent="${esc(id)}">Switch to ${esc(otherMode)}</button>`,
    `<button class="ghost" data-agent-edit="${esc(id)}">Edit…</button>`,
    `<button class="ghost" data-agent-history="${esc(id)}">History</button>`,
    sid ? `<button class="ghost danger" data-agent-delete-session="${esc(sid)}">Delete session</button>` : '',
    `<button class="ghost danger" data-agent-remove="${esc(id)}">Remove agent</button>`,
    `<button class="ghost" data-agent-open-sessions="${esc(sid)}">Open in Sessions</button>`,
  ].filter(Boolean).join('');
  // Always render this block. When there is no command, say WHY — an absent section is
  // indistinguishable from a broken feature (operator report: "llama-manager does not have cli
  // command that i can copy"; it has no session handle, so there is nothing to resume).
  const cli = continueCliDetails(agent, session);
  const cliCmd = cli.command;
  const continueCliBlock = `
      <div class="agent-drawer-cli">
        <div class="agent-drawer-subhead">Continue in CLI</div>
        ${cliCmd
          ? `<p class="subtle">Resume this session in your own terminal — native ${esc(agent.runtime || 'runtime')} CLI.</p>
        <p class="subtle cli-cmd-machine">${esc(resumeMachineNote(cli.machine))}</p>
        <div class="cli-cmd-row"><code class="cli-cmd">${esc(cliCmd)}</code><button class="ghost" data-copy-cli="${esc(cliCmd)}" title="Copy the resume command">Copy</button></div>`
          : `<p class="subtle">${esc(cli.reason)}</p>`}
      </div>`;
  // HOW LONG AGO THIS AGENT WAS LAST HEARD FROM. The age was already in the drawer -- inside the
  // status chip's `title`, built by `statusWhyContext` -- so this is not "the drawer never said".
  // It said it only on hover, over a chip reading `available`, which gives nobody a reason to hover.
  //
  // MEASURED on the operator's fleet 2026-08-29: 18 of 47 agents had been silent for more than 30
  // days, three of them for 120, and TWO of those still read `available`. That status is honest --
  // the environment can cold-start them -- and it is the same word an agent that answered forty
  // seconds ago carries. `environments-panels.mjs` makes the identical argument for hosts: "A host
  // that dropped a minute ago and one abandoned in June render identically as `offline`, and those
  // call for opposite actions."
  //
  // FAILS CLOSED: `relTime` returns '' for a missing or unparseable timestamp, so the row renders an
  // em dash and claims nothing, rather than "last seen  ago" or an age measured from the epoch.
  // `lastSeen` ONLY. The `|| agent.last_seen` alternate I wrote here first is a field this
  // service does not emit, and `test_the_dashboard_reads_only_agent_fields_the_service_emits`
  // reddened on it -- correctly. That gate exists because three such alternates were removed
  // in one sweep: "a dead alternate is worse than nothing here, because it reads like
  // coverage for the rename it cannot catch". I added a fourth and a test asserting it works.
  // Emitted with its own timestamp so `rel-time-ticker.mjs` can keep it true without repainting
  // the drawer -- which would destroy the operator's selection mid-read.
  const lastSeen = relTimeHtml(agent.lastSeen);
  const sessionChangedBanner = agent.sessionChanged ? `
      <div class="session-changed-banner" role="alert">
        <p>⚠ This agent reported a new session id <code>${esc(agent.pendingSessionId)}</code> that differs from its pinned handle <code>${esc(agent.sessionHandle || '—')}</code>. Delivery still targets the pinned handle until you resolve this.</p>
        <div class="button-row">
          <button class="primary" data-session-confirm="${esc(id)}">Confirm new id</button>
          <button class="ghost" data-session-keep="${esc(id)}">Keep pinned</button>
        </div>
      </div>` : '';
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer">
      <div class="agent-drawer-head"><strong>${esc(id)}</strong>${renderStatusChip(agent.status || 'unknown', statusWhyContext('agent', agent, agent.status))}</div>
      ${sessionChangedBanner}
      <dl class="chat-kv agent-drawer-kv">
        ${row('Runtime', esc(agent.runtime || (session && sessionRuntime(session)) || '—'))}
        ${row('Mode', esc(mode))}
        ${row('Environment', esc((env && (env.label || env.id)) || sessionEnvironmentId(session) || '—'))}
        ${row('Workspace', esc((session && session.workspace) || agent.cwd || '—'))}
        ${row('Session', sid ? `${esc(sid)} · ${esc(session.status || 'unknown')}` : '<span class="subtle">no active session</span>')}
        ${row('Machine', esc(agent.machineId || '—'))}
        ${row('Last seen', lastSeen ? `${lastSeen} ago` : '—')}
      </dl>
      ${continueCliBlock}
      <div id="${AGENT_SHARING_ID}"></div>
      <div class="agent-drawer-cli">
        <div class="agent-drawer-subhead">Processes</div>
        <div id="${AGENT_PROCESSES_ID}"><p class="subtle">Reading this agent's terminals…</p></div>
      </div>
      <div class="agent-drawer-cli">
        <div class="agent-drawer-subhead">Recent runs</div>
        <div id="${AGENT_RUNS_ID}"></div>
      </div>
      <div class="agent-drawer-actions">${actions}</div>
    </div>`;
  // Remember WHICH agent the drawer is showing, so selecting a different agent can follow it
  // (see syncInspectorToSelection) instead of leaving a stale panel open on the previous agent.
  state.inspector = { ...state.inspector, kind: 'agent', runId: '', agentId: id };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
  // B5: WHAT IS ACTUALLY RUNNING FOR THIS AGENT, fetched after the drawer is on screen rather than
  // polled with the other nine endpoints -- "browse" is a deliberate act, so the read is too. Fire
  // and forget: `loadAgentProcesses` renders its own failure into its own panel, and everything
  // else in this drawer stays true whether that read succeeds or not.
  loadAgentProcesses(id, { api, byId });
  fillAgentRuns(id, { byId });
  fillSessionSharing(id, { byId, agents: state.agents });
}
export function syncInspectorToSelection() {
  const inspector = byId('inspector');
  if (!inspector?.classList.contains('open')) return;
  if (state.inspector?.kind !== 'agent') return;
  const selected = String(state.chat?.selected || '');
  const shownAgent = String(state.inspector?.agentId || '');
  if (!selected || !selected.startsWith('dm:')) {
    inspector.classList.remove('open');
    state.inspector = { ...state.inspector, kind: '', agentId: '' };
    return;
  }
  const nextAgent = selected.slice('dm:'.length);
  if (!nextAgent || nextAgent === shownAgent) return;
  openAgentDrawer(nextAgent);
}
