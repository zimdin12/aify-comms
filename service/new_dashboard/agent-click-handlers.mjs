// Click-handler bodies for the agent lifecycle controls.
//
// Both lived inside app.js's delegated click handler and were unreachable by any test. `startColdAgent`
// is the one worth having covered: it disables its own button and rewrites its label before an async
// call, so every failure path has to put both back or the control stays dead until a re-render.
//
// The callbacks are INJECTED — `refreshSoon` and `switchAgentSessionMode` stay in app.js — and
// `switchModeFromChip` takes `event` for the same reason, since it suppresses the default and stops
// propagation before doing anything.

import { api } from './api-client.mjs';
import { requestedInAifyEnv } from './definition-request-note.mjs';
import { resolveStatus } from './status.js';
import { toast } from './ui.js';
import { esc } from './util.js';
import { state } from './state.mjs';

/**
 * Whether the dashboard offers Start for this agent, and why not when it does not (v0.7.7).
 *
 * The drawer and the Start dialog both ask this. It decides when to OFFER Start, not whether the
 * start succeeds: `POST /agents/{id}/control` still refuses what it cannot run, and the caller shows
 * that refusal. A missing mode is resident, as the route reads it, and the route refuses a resident.
 * Only `stopped` and `available` are offered: a live agent has a worker, `starting` has one on the
 * way, and `misconfigured` cannot start until a human fixes it. Narrower than aify-env's
 * `startabilityOf`, which also offers an offline agent on its own host.
 */
export function startOffer(agent) {
  const mode = String(agent?.sessionMode || 'resident').toLowerCase();
  const status = resolveStatus(agent?.status).kind;
  if (mode !== 'managed') {
    return { start: false, why: 'resident: its terminal is the CLI you launched. Switch it to managed to start it here.' };
  }
  if (status === 'stopped' || status === 'available') return { start: true, why: '' };
  if (status === 'starting') return { start: false, why: 'already starting' };
  if (status === 'misconfigured') return { start: false, why: 'misconfigured: fix its configuration first' };
  return { start: false, why: '' };
}

/**
 * The drawer's herdr-space switch for a managed agent (2026-09-30). `herdrSpace` false starts the agent
 * without a herdr space of its own; it still runs, shows here, and can be attached from aify-env.
 * A record from a service without the field shows a space, as the service defaults it.
 */
export function herdrSpaceButton(agent) {
  const shown = agent?.herdrSpace !== false;
  const id = String(agent?.id || '');
  const title = 'Applies from its next start. The agent still runs, shows here, and can be attached from aify-env.';
  return `<button class="ghost" data-agent-herdr-space="${esc(id)}" data-show="${shown ? 'false' : 'true'}" title="${title}">${shown ? 'Hide from herdr' : 'Show in herdr'}</button>`;
}

/**
 * THE ACKNOWLEDGED VALUE IS SHOWN AT ONCE (review of 896abb17). Leaving the button to the next roster
 * fetch left it disabled and offering the old choice whenever that fetch failed: the refresh keeps the
 * old roster, and the drawer does not repaint unchanged markup. So the agent in `state` and this
 * button both take the value the service answered with, and the button is usable again.
 */
export function setHerdrSpace(button, refreshSoon) {
  const id = button.dataset.agentHerdrSpace;
  const show = button.dataset.show === 'true';
  button.disabled = true;
  return api(`/agents/${encodeURIComponent(id)}/herdr-space`, { method: 'PATCH', body: JSON.stringify({ show }) })
    .then((answer) => {
      const requested = requestedInAifyEnv(answer, id);
      if (requested) {
        button.disabled = false;
        toast(requested, 'ok');
        refreshSoon();
        return;
      }
      const stored = typeof answer?.herdrSpace === 'boolean' ? answer.herdrSpace : show;
      const agent = state.agents.find((a) => a.id === id);
      if (agent) agent.herdrSpace = stored;
      button.dataset.show = stored ? 'false' : 'true';
      button.textContent = stored ? 'Hide from herdr' : 'Show in herdr';
      button.disabled = false;
      toast(stored ? `${id} gets a herdr space from its next start` : `${id} starts without a herdr space from its next start`, 'ok');
      refreshSoon();
    })
    .catch((err) => {
      toast(`Herdr setting failed: ${err?.message || err}`, 'error');
      button.disabled = false;
    });
}

export function startColdAgent(agentAction, refreshSoon) {
  const id = agentAction.dataset.agentId;
  // Put back what the button said, whichever surface drew it ("Start" or "Start agent").
  const label = agentAction.textContent;
  agentAction.disabled = true;
  agentAction.textContent = 'Starting…';
  api(`/agents/${encodeURIComponent(id)}/control`, { method: 'POST', body: JSON.stringify({ action: 'start', from_agent: 'dashboard' }) })
    .then((r) => {
      toast(requestedInAifyEnv(r, id) || (r?.alreadyRunning ? `${id} is already running` : `Starting ${id} — the console appears once its worker is up`), 'ok');
      refreshSoon();
    })
    .catch((err) => {
      toast(`Start agent failed: ${err?.message || err}`, 'error');
      agentAction.disabled = false;
      agentAction.textContent = label;
    });
}

export function switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode) {
  event.preventDefault();
  event.stopPropagation();
  const agentId = modeSwitchButton.dataset.modeSwitch;
  const targetMode = modeSwitchButton.dataset.targetMode;
  switchAgentSessionMode(agentId, targetMode);
}

// Three more row-level agent controls. Two of them return PROMISES into a click handler and attach
// their own `.catch` — without it an unhandled rejection surfaces as an unrelated console error and the
// operator sees nothing where a failure message belongs.
export function toggleFavouriteRow(favToggle, event, toggleFavorite) {
  event.stopPropagation();
  toggleFavorite(favToggle.dataset.favToggle);
}

export function runAgentControl(agentControl, requestSessionControl) {
  requestSessionControl(agentControl.dataset.session, agentControl.dataset.agentControl)
    .catch((err) => toast(`Action failed: ${err?.message || err}`, 'error'));
}

export function switchAgentModeFromRow(agentMode, switchAgentSessionMode) {
  switchAgentSessionMode(agentMode.dataset.agent, agentMode.dataset.agentMode)
    .catch((err) => toast(`Mode switch failed: ${err?.message || err}`, 'error'));
}
