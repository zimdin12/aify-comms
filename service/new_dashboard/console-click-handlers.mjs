// The console toolbar's actions, moved out of app.js's delegated click handler in v0.5.4.
//
// One body, and it is a five-way dispatch on a data attribute — which is exactly the shape that rots
// quietly. An unrecognised action falls through every branch and does NOTHING, with no error anywhere,
// so a renamed attribute in the template turns a toolbar button into a no-op that looks fine in review.
//
// `copyActiveConsole` and `toast` are imported because they already live in modules; the three that
// still live in app.js are injected under their own names, which keeps the body byte-identical.

import { closeConsoleFind, stepConsoleFind, toggleConsoleFind } from './console-find.mjs';
import { copyActiveConsole } from './clipboard.mjs';
import { toast } from './ui.js';

export function runConsoleAction(consoleAction, resyncActiveConsole, stopConsoleTerminal, startConsoleForSession) {
  const action = consoleAction.dataset.consoleAction;
  if (action === 'copy') copyActiveConsole();
  else if (action === 'refresh') resyncActiveConsole({ forceRepaint: true }).then(() => toast('Console refreshed', 'ok')).catch(() => {});
  else if (action === 'stop') stopConsoleTerminal(consoleAction.dataset.terminalId);
  else if (action === 'start') startConsoleForSession(consoleAction.dataset.sessionId, false);
  else if (action === 'start-fresh') startConsoleForSession(consoleAction.dataset.sessionId, true);
  // FIND, and its three bar buttons. The host is resolved from the button rather than by a global
  // id for the same reason the mount is: a Chat-embedded console and the Sessions console are two
  // live consoles on one page, and a global lookup would drive whichever came first in the DOM.
  else if (action === 'find') toggleConsoleFind(consoleHost(consoleAction));
  else if (action === 'find-next') stepConsoleFind(consoleHost(consoleAction), 1);
  else if (action === 'find-prev') stepConsoleFind(consoleHost(consoleAction), -1);
  else if (action === 'find-close') closeConsoleFind(consoleHost(consoleAction));
  // AN UNKNOWN ACTION IS LOUD NOW. This file's own header warned that a renamed attribute in the
  // template turns a toolbar button into a silent no-op that looks fine in review -- and the
  // warning sat above a dispatch that did exactly that. Returning the action lets
  // `console-actions-agree-with-the-template.test.mjs` drive every value the markup actually emits
  // and fail on one nothing handles, so the population is derived rather than remembered.
  else return null;
  return action;
}

/**
 * The console this button belongs to.
 *
 * `closest` walks up to the embed that holds both the toolbar and the find bar. A test passes a
 * fake button, so the fallback is the button itself rather than a throw.
 */
function consoleHost(el) {
  return el?.closest?.('.console-embed') ?? el;
}
