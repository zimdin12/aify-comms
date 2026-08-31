// Keep "4m ago" true without repainting the section it sits in.
//
// THE PROBLEM, in `render-memo.mjs`'s own words: relative times "must change as the clock advances
// even when no datum does. No signature can express that: adding `lastSeen` repaints only when the
// heartbeat lands, and adding a clock tick repaints every section on a timer and destroys selection
// and focus while doing it."
//
// So neither. Each rendered time carries its own absolute instant in `data-rel-ts` (see
// `relTimeHtml`), and this ticker rewrites `textContent` in place. No innerHTML, no rebuilt nodes,
// nothing for the memo to be asked about -- the section is never re-rendered at all.
//
// WHY IT MATTERS BEYOND TIDINESS: `lastSeen` is the field an operator reads to decide whether an
// agent is alive. A frozen "1m ago" beside a dead agent is not a cosmetic defect, it is the display
// asserting the opposite of the truth, and it is the exact reading that sent this session chasing a
// live-looking timestamp on 2026-08-31.

import { relTimeAt } from "./util.js";

/** The class `relTimeHtml` stamps and this ticker collects. One name, declared once. */
export const REL_TIME_SELECTOR = ".rel-time";

/** How often the labels are re-derived. A minute-resolution label needs no faster tick. */
export const REL_TIME_INTERVAL_MS = 15000;

/**
 * Re-derive every element's label from the instant it stored, at ONE reading of the clock.
 *
 * PURE OVER ITS ARGUMENTS: the elements and the moment are both handed in, so this is testable
 * without a browser and without waiting for a timer.
 *
 * A NODE WITH NO USABLE TIMESTAMP IS LEFT EXACTLY AS IT IS. Failing closed matters here: overwriting
 * on missing input would turn one absent attribute into a wiped label, which an operator reads as
 * "no data" rather than as a bug.
 *
 * @returns {number} how many elements actually changed.
 */
export function refreshRelTimes(elements, nowMs) {
  if (!elements) return 0;
  let changed = 0;
  for (const element of elements) {
    const raw = element?.dataset?.relTs;
    if (raw === undefined || raw === null || raw === "") continue;
    const stored = Number(raw);
    if (!Number.isFinite(stored)) continue;
    const next = relTimeAt(stored, nowMs);
    if (!next) continue;
    // Only write when it differs. A textContent assignment is a DOM mutation under an operator who
    // may be mid-selection, and the point of this whole module is to stop doing that needlessly.
    if (element.textContent !== next) {
      element.textContent = next;
      changed += 1;
    }
  }
  return changed;
}

/**
 * Start re-deriving the labels, and hand back the way to stop.
 *
 * THE NODE LIST IS RE-QUERIED EVERY TICK, never captured once. Sections repaint and replace their
 * elements; a ticker holding the originals would go quietly dead on exactly the sections that change
 * most, which is the failure shape this repo keeps finding.
 *
 * Timers and the clock are injected so the whole driver -- not just the helper it calls -- can be
 * driven in a test. A green helper beside an unproven call site is how a feature ships that can
 * never fire.
 */
export function startRelTimeTicker({
  queryAll,
  nowMs = () => Date.now(),
  intervalMs = REL_TIME_INTERVAL_MS,
  setIntervalImpl = setInterval,
  clearIntervalImpl = clearInterval,
} = {}) {
  const paint = () => {
    // One bad frame must not stop every relative time on the page for the rest of the session --
    // the latching failure `renderSection` was fixed for, in a loop that has no such recovery.
    try {
      refreshRelTimes(queryAll(), nowMs());
    } catch { /* next tick tries again */ }
  };

  paint();
  const handle = setIntervalImpl(paint, intervalMs);
  // NEVER HOLD A PROCESS OPEN ON THIS TICKER'S ACCOUNT. In a browser `setInterval` returns a number
  // and this is a no-op; under Node it returns a Timeout that keeps the event loop alive, and a test
  // that merely WIRES the boot sequence then hangs forever instead of finishing. That is exactly what
  // happened the first time this module was wired in, and a test runner that never exits reports
  // nothing at all -- the worst way for a suite to fail. `liveness-heartbeat.js` guards the same way.
  handle?.unref?.();
  return function stop() {
    clearIntervalImpl(handle);
  };
}
