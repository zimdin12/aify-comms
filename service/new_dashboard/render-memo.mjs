// Skip a re-render when nothing it depends on changed. Moved out of app.js in v0.5.4.
//
// The dashboard polls, so every section is asked to re-render on a timer whether or not its data moved.
// Rendering anyway is not merely wasteful: it destroys and rebuilds DOM under an operator who may be
// mid-selection, mid-scroll or mid-dropdown. This is the memo that stops that, and its correctness is
// entirely in the signature — too coarse and it re-renders constantly, too narrow and it goes blind to
// a real change.

export const _sectionSig = Object.create(null);

import { state } from './state.mjs';
export function renderSection(key, signature, renderFn) {
  const sig = JSON.stringify(signature);
  if (_sectionSig[key] === sig) return;
  _sectionSig[key] = sig;
  renderFn();
}

// The signature builders `renderSection` compares, moved out of app.js in v0.5.4. They belong beside it
// because they ARE the memo's correctness: each names the fields whose change should repaint a section,
// so a field left out makes that section go blind to a real update, and an unstable one makes it repaint
// on every poll. The memo itself cannot tell the difference.
export const _agentSig = () => state.agents.map((a) => [a.id, a.status]);
export const _contractSig = () => state.contracts.map((c) => [c.id, c.state, c.status, c.overdue, c.subject]);
export const _runSig = () => state.runs.map((r) => [r.id, r.status, r.subject, r.summary, r.targetAgentId || r.target_agent]);
export const _envSig = () => state.environments.map((e) => [e.id, e.status, e.label]);
export const _spawnReqSig = () => state.spawnRequests.map((r) => [r.id, r.status, r.agentId, r.error, r.updatedAt]);
export const _msgSig = () => state.messages.map((m) => [m.id, m.from, m.subject, m.read]);
export const _chatChanSig = () => (state.chat.channels || []).map((c) => [c.name, c.unreadCount, c.memberCount]);
export const _chatConvSig = () => Object.entries(state.chat.channelMessages || {}).map(([k, v]) => [k, (v || []).length]);
