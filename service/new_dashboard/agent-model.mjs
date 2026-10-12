// The model an agent is set to, beside the one its session actually runs, in the drawer (0.9.1 3B).
//
// READ WHEN THE DRAWER OPENS, never polled (Steven, 2026-10-12: "it should be on demand"). `/agents/{id}/model`
// asks the agent's host, which reads the harness's own record of that session. A host that does not answer in
// time gets its last answer shown with that said; nothing is ever shown as running that was not read.
//
// NO MISMATCH VERDICT: a setting is often an alias (`opus`) or empty (the harness default), and neither can be
// compared with a dated id by spelling. Both values are shown and the person judges.

import { state } from './state.mjs';
import { esc, relTimeHtml } from './util.js';
import { paintIfChanged } from './drawer-paint.mjs';

/** The container the drawer leaves for this row, so both sides name it once. */
export const AGENT_MODEL_ID = 'agent-drawer-model';

const subtle = (text) => `<span class="subtle">${text}</span>`;

/** What the agent is set to run: `runsWith`, the agent record's projection; empty is the harness default. PURE. */
function configured(runsWith) {
  const model = String(runsWith?.model?.value || '');
  const effort = String(runsWith?.effort?.value || '');
  return `set: ${model ? esc(model) : subtle('harness default')}${effort ? ` · ${esc(effort)}` : ''}`;
}

/** What it actually runs, from `/agents/{id}/model`'s `actual`, or why that is not known. PURE. */
function running(actual, error) {
  if (error) return subtle(`runs: could not ask (${esc(error)})`);
  if (!actual) return subtle('runs: asking its host…');
  if (!actual.model) return subtle(`runs: unknown, ${esc(actual.unknown || 'no reading')}`);
  const answered = relTimeHtml(actual.at);
  return `runs: <code>${esc(actual.model)}</code>${answered ? ` ${subtle(`(answered ${answered} ago)`)}` : ''}`
    + (actual.stale ? ` ${subtle(`⚠ ${esc(actual.stale)}`)}` : '');
}

/** The drawer's Model row. PURE. */
export function renderAgentModel(runsWith, actual, { error = '' } = {}) {
  return `${configured(runsWith)} · ${running(actual, error)}`;
}

//: The host's last answer per agent, so a drawer repainted for another reason keeps showing it while the host is asked
//: again, instead of blinking back to "asking". The setting is never kept: it is always the record's current one.
//: ponytail: one entry per agent opened this page load.
const answers = new Map();

/** The row to paint before the host answers: the current setting beside the last answer, else "asking". */
export function modelRowBeforeAnswer(agentId, runsWith) {
  const last = answers.get(agentId);
  return renderAgentModel(runsWith, last?.actual ?? null, { error: last?.error ?? '' });
}

/** Fill the row for one agent. Never throws; writes only while the drawer is still on that agent. */
export async function loadAgentModel(agentId, { api, byId, runsWith } = {}) {
  const id = String(agentId || '').trim();
  const host = typeof byId === 'function' ? byId(AGENT_MODEL_ID) : null;
  if (!host || !id || typeof api !== 'function') return;
  let answer;
  try {
    answer = { actual: (await api(`/agents/${encodeURIComponent(id)}/model`))?.actual ?? null, error: '' };
  } catch (err) {
    answer = { actual: null, error: String(err?.message || err) };
  }
  if (String(state?.inspector?.agentId || '') !== id) return;
  answers.set(id, answer);
  paintIfChanged(host, renderAgentModel(runsWith, answer.actual, { error: answer.error }));
}
