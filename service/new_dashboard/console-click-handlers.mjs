// The console toolbar's actions, moved out of app.js's delegated click handler in v0.5.4.
//
// One body, and it is a five-way dispatch on a data attribute — which is exactly the shape that rots
// quietly. An unrecognised action falls through every branch and does NOTHING, with no error anywhere,
// so a renamed attribute in the template turns a toolbar button into a no-op that looks fine in review.
//
// `copyActiveConsole` and `toast` are imported because they already live in modules; the three that
// still live in app.js are injected under their own names, which keeps the body byte-identical.

import { copyActiveConsole } from './clipboard.mjs';
import { toast } from './ui.js';

export function runConsoleAction(consoleAction, resyncActiveConsole, stopConsoleTerminal, startConsoleForSession) {
  const action = consoleAction.dataset.consoleAction;
  if (action === 'copy') copyActiveConsole();
  else if (action === 'refresh') resyncActiveConsole({ forceRepaint: true }).then(() => toast('Console refreshed', 'ok')).catch(() => {});
  else if (action === 'stop') stopConsoleTerminal(consoleAction.dataset.terminalId);
  else if (action === 'start') startConsoleForSession(consoleAction.dataset.sessionId, false);
  else if (action === 'start-fresh') startConsoleForSession(consoleAction.dataset.sessionId, true);
}
