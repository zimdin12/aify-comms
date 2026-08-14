// The console "awaiting input" pill and the prompt heuristic behind it, moved out of app.js in v0.5.4.
//
// The pill tells an operator that an agent is sitting on a prompt rather than working. Two signals feed
// it and they are NOT equivalent: the server-derived `blocked` status is authoritative, and the tail
// regex is a fallback for consoles the status engine does not classify — plain bash, for instance. A
// false negative here reads as an agent that has silently stalled; a false positive puts a "waiting"
// badge on an agent that is working.

import { state } from './state.mjs';
import { byId } from './ui.js';

export function consoleAwaitingInputHint(text) {
  const tail = String(text || '').slice(-400).toLowerCase();
  if (!tail.trim()) return false;
  return /\((y\/n|yes\/no)\)|press enter|are you sure|continue\?|\[y\/n\]|overwrite\?|proceed\?/.test(tail);
}

export function updateAwaitPill() {
  const pill = byId('console-await-pill');
  if (!pill) return;
  // Server-derived `blocked` (a real prompt paused the agent's spinner) is the
  // authoritative signal; the tail regex only catches generic y/n prompts the
  // status engine doesn't classify (e.g. plain-bash consoles).
  const agent = state.agents.find((a) => a.id === state.activeXterm?.agentId);
  const blocked = String(agent?.status || '').startsWith('blocked');
  pill.hidden = !blocked && !consoleAwaitingInputHint(state.activeXterm?.recentText || '');
}
