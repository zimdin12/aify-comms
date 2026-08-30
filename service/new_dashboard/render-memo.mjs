// Skip a re-render when nothing it depends on changed. Moved out of app.js in v0.5.4.
//
// The dashboard polls, so every section is asked to re-render on a timer whether or not its data moved.
// Rendering anyway is not merely wasteful: it destroys and rebuilds DOM under an operator who may be
// mid-selection, mid-scroll or mid-dropdown. This is the memo that stops that, and its correctness is
// entirely in the signature — too coarse and it re-renders constantly, too narrow and it goes blind to
// a real change.

export const _sectionSig = Object.create(null);

import { noteSliceFailure } from './refresh-status.mjs';
import { state } from './state.mjs';
/**
 * Render one section if its inputs moved, and never let it take the others with it.
 *
 * **THE SIGNATURE IS RECORDED BEFORE THE RENDER, DELIBERATELY.** A renderer that synchronously
 * re-enters `renderSection` for its own key would recurse forever otherwise, and the test directly
 * below provokes exactly that rather than reasoning about it. My first attempt at this fix moved the
 * write after the render and that test caught it -- which is the test doing its job, and the reason
 * the ordering is now stated here instead of merely being true.
 *
 * WHAT WAS ACTUALLY WRONG was that nothing UNDID the record when the render threw. The memo then said
 * "this state is already drawn" for a state that never was, and the section stayed blank until its
 * data changed AGAIN, because every following cycle compared equal and returned early. One bad frame
 * latched a panel off, and the fix for the operator was to wait for unrelated data to move.
 *
 * AND THERE WAS NO try/catch, in a loop of eleven sections called in order. A throw in section two
 * meant sections three to eleven never rendered that cycle: metrics, attention, activity, contracts,
 * environments, runtime, spawn requests, runs, files, settings. One malformed row blanked most of the
 * dashboard, and the memo then hid the recovery.
 *
 * `renderAttention` carries the comment "never let a missing node throw out of the unconditional
 * renderAll loop" and guards its host node accordingly. Measured across the loop: 7 of the 11 sections
 * do that and 3 write a node without checking it exists. Guarding those three would cover the
 * missing-node case alone; this covers every way a render can throw, which is the class that comment
 * is really about.
 *
 * A FAILED SECTION IS RETRIED next cycle rather than remembered as done. One that throws every time
 * reports every time -- noisy and correct, because a silently blank panel is the failure this repo
 * keeps finding and noise can at least be read.
 */
export function renderSection(key, signature, renderFn) {
  const sig = JSON.stringify(signature);
  if (_sectionSig[key] === sig) return;
  // BEFORE the render: the re-entrancy guard. See above.
  _sectionSig[key] = sig;
  try {
    renderFn();
  } catch (error) {
    // A render that did not finish is not drawn. DELETE rather than restore the previous value: the
    // old signature would also compare unequal next cycle, but only until the data drifted back to
    // it, and "retry until it works" must not depend on that.
    delete _sectionSig[key];
    noteSliceFailure(`render:${key}`);
    // Reported AND named. The slice list is what the connection chip drains, so the operator learns
    // which section failed; the console line is the only place the actual error survives.
    try { console.error(`[dashboard] section "${key}" failed to render:`, error); } catch { /* no console */ }
  }
}

// The signature builders `renderSection` compares, moved out of app.js in v0.5.4. They belong beside it
// because they ARE the memo's correctness: each names the fields whose change should repaint a section,
// so a field left out makes that section go blind to a real update, and an unstable one makes it repaint
// on every poll. The memo itself cannot tell the difference.
// WHAT THESE MISSED, AND WHY IT MATTERED. `_agentSig` was `[id, status]` and `_envSig` was
// `[id, status, label]`, while the sections they gate render a good deal more than that. A field the
// renderer reads and the signature omits makes that section go BLIND: the data changes, the memo
// sees an identical signature, and the screen keeps showing the old answer with nothing indicating
// it is stale. `staleBridgeBadge` is the sharpest case — it exists because an operator restarted
// aify-env twice against a bridge 231 commits behind the service and "nothing on any screen said
// so", and it is rendered from `metadata.bridgeBuild`, which `_envSig` did not carry. The badge
// built to end that silence could not appear.
//
// RELATIVE TIMES ARE DELIBERATELY NOT SOLVED HERE. `offlineAge` renders "last seen 4m ago", which
// must change as the clock advances even when no datum does. No signature can express that: adding
// `lastSeen` repaints only when the heartbeat lands, and adding a clock tick repaints every section
// on a timer and destroys selection and focus while doing it. It needs the times to stop being baked
// into `innerHTML` at all. Left as v0.6.1 work rather than half-done here.
export const _agentSig = () => state.agents.map((a) => [
  a.id, a.status, a.statusNote, a.runtime, a.sessionMode, a.role, a.model,
  a.unread, a.favorited, a.consoleAvailable, a.quotaCritical, a.poolSeverity,
]);
export const _contractSig = () => state.contracts.map((c) => [c.id, c.state, c.status, c.overdue, c.subject]);
export const _runSig = () => state.runs.map((r) => [r.id, r.status, r.subject, r.summary, r.targetAgentId || r.target_agent]);
export const _envSig = () => state.environments.map((e) => [
  e.id, e.status, e.label, e.kind, e.os, e.machineId, e.terminal, e.pty,
  // The metadata keys `environments-panels.mjs` actually reads. The blob is NOT stringified whole:
  // it also carries `bridgeLastSeen`, which is rewritten every 30 seconds by the environment
  // heartbeat, so a whole-blob signature would repaint both environment sections on every poll.
  e.metadata?.bridgeBuild, e.metadata?.unknownProcesses, e.metadata?.terminalReason,
  e.metadata?.manual, JSON.stringify(e.metadata?.manualRoots ?? null),
  // Runtime pills and workspace roots are rendered per environment and change when a host is
  // re-described — which, since aify-env began advertising, happens without the status moving.
  JSON.stringify(e.runtimes ?? null), JSON.stringify(e.cwdRoots ?? null),
]);
export const _spawnReqSig = () => state.spawnRequests.map((r) => [r.id, r.status, r.agentId, r.error, r.updatedAt]);
export const _msgSig = () => state.messages.map((m) => [m.id, m.from, m.subject, m.read]);
export const _chatChanSig = () => (state.chat.channels || []).map((c) => [c.name, c.unreadCount, c.memberCount]);
export const _chatConvSig = () => Object.entries(state.chat.channelMessages || {}).map(([k, v]) => [k, (v || []).length]);
