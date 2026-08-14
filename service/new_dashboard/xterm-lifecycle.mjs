// Tearing down the one live xterm instance.
//
// Extracted from app.js in v0.5.4 — the last declaration in that file with a closure of its own; every
// other remaining function reaches the render orchestrator.
//
// EVERY STEP IS SEPARATELY BEST-EFFORT, and that is the whole design. A console can be disposed while its
// container is already gone from the DOM, or after xterm has torn itself down, so any one step can throw.
// Wrapping them individually means a failure in the first does not skip the rest — and above all does not
// skip `state.activeXterm = null`, which is what lets the next console mount. Leave a stale entry there
// and the next mount believes an xterm is already live.
//
// It does not join `terminal-input.mjs` (pure helpers with injected dependencies, touching no shared
// state) or `codex-console.mjs` (whose header records that it needs nothing at all, not even a sibling
// module). Both tests are the ones used throughout this series: do not put state-touching behaviour into a
// module whose stated character is that it has none.

import { state } from './state.mjs';

export function disposeActiveXterm() {
  const entry = state.activeXterm;
  if (!entry) return;
  try { entry.resizeObserver?.disconnect(); } catch {}
  try { if (entry.wheelHandler && entry.container) entry.container.removeEventListener('wheel', entry.wheelHandler); } catch {}
  try { entry.term.dispose(); } catch {}
  state.activeXterm = null;
}
