// Which controls a run's inspector may offer, and the markup for them.
//
// `runInspectorCapabilities` is a pure decision: six flags derived from the run's resolved status and
// whether it has a target agent. It decides what the operator can DO to a run, and every flag is a way to
// get it wrong in a direction that matters — offering Steer on a finished run sends input nowhere;
// withholding Close on a stuck one leaves no way to clear it; offering Open console without a session
// opens an empty panel.
//
// Extracted from app.js in v0.5.4 as a measured closure needing three names from sibling leaf modules.
// The run inspector's RENDERER stays behind: it calls `evaluateFlowGates`, whose `flowGates` entries probe
// half of app.js, and a slice that took that group was written, proven and reverted earlier in this series
// (see docs/APP_JS_STATE_MODULE_PACKET.md, fifth correction). The controls separate cleanly from it; the
// renderer does not.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `. Their leading comments stayed behind — `declarationSpan` returns the declaration alone, so a
// span carrying its comments could not round-trip through the reconstruction proof.


import { sessionForAgent } from './agent-drawer.mjs';
import { runTargetAgent } from './record-fields.mjs';
import { resolveStatus } from './status.js';

export function sessionForRun(run) {
  return sessionForAgent(runTargetAgent(run));
}
export function runInspectorCapabilities(run, session = sessionForRun(run)) {
  const statusKind = resolveStatus(run?.status).kind;
  const active = ['claimed', 'running'].includes(statusKind);
  const terminal = ['completed', 'failed', 'cancelled'].includes(statusKind);
  const target = runTargetAgent(run);
  return {
    steer: Boolean(active),
    interrupt: Boolean(active),
    queueAfter: Boolean(target),
    retry: Boolean(target),
    close: Boolean(run?.id && !terminal),
    openConsole: Boolean(session),
  };
}
export function renderRunInspectorControls(run) {
  const session = sessionForRun(run);
  const capabilities = runInspectorCapabilities(run, session);
  const disabled = (enabled) => enabled ? '' : ' disabled';
  return `
    <div id="run-inspector-controls" class="run-inspector-controls">
      <button class="ghost" data-run-control="steer"${disabled(capabilities.steer)} title="Steer">Steer</button>
      <button class="ghost danger" data-run-control="interrupt"${disabled(capabilities.interrupt)} title="Interrupt">Interrupt</button>
      <button class="ghost" data-run-control="queue-after"${disabled(capabilities.queueAfter)} title="Queue-after">Queue-after</button>
      <button class="ghost danger" data-run-control="retry"${disabled(capabilities.retry)} title="Retry">Retry</button>
      <button class="ghost danger" data-run-control="close"${disabled(capabilities.close)} title="Close">Close</button>
      <button class="primary" data-run-control="open-console"${disabled(capabilities.openConsole)} title="Open Console">Open Console</button>
    </div>`;
}
