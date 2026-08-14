// Click-handler bodies for the agent lifecycle controls.
//
// Both lived inside app.js's delegated click handler and were unreachable by any test. `startColdAgent`
// is the one worth having covered: it disables its own button and rewrites its label before an async
// call, so every failure path has to put both back or the control stays dead until a re-render.
//
// The callbacks are INJECTED — `refreshSoon` and `switchAgentSessionMode` stay in app.js — and
// `switchModeFromChip` takes `event` for the same reason, since it suppresses the default and stops
// propagation before doing anything. Parameters of the same names leave both bodies byte-identical.

import { api } from './api-client.mjs';
import { toast } from './ui.js';

export function startColdAgent(agentAction, refreshSoon) {
  const id = agentAction.dataset.agentId;
  agentAction.disabled = true;
  agentAction.textContent = 'Starting…';
  api(`/agents/${encodeURIComponent(id)}/control`, { method: 'POST', body: JSON.stringify({ action: 'start', from_agent: 'dashboard' }) })
    .then((r) => {
      toast(r?.alreadyRunning ? `${id} is already running` : `Starting ${id} — the console appears once its worker is up`, 'ok');
      refreshSoon();
    })
    .catch((err) => {
      toast(`Start agent failed: ${err?.message || err}`, 'error');
      agentAction.disabled = false;
      agentAction.textContent = 'Start agent';
    });
}

export function switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode) {
  event.preventDefault();
  event.stopPropagation();
  const agentId = modeSwitchButton.dataset.modeSwitch;
  const targetMode = modeSwitchButton.dataset.targetMode;
  switchAgentSessionMode(agentId, targetMode);
}
