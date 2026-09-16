// The dashboard's data refresh runs only while someone can see the page.
//
// MEASURED 2026-09-16, on an idle host: a dashboard tab the operator did not have open in front of them
// -- a background tab in another Edge window -- kept requesting a full poll bundle (ten reads, /agents
// and /stats among them) once a minute, which is the fastest a browser lets a hidden tab's timer run.
// Realtime events reaching that tab asked for the same bundle again. Nobody could see any of it.
//
// A hidden page now records that it is STALE instead of fetching, and refreshes once when it becomes
// visible again, so a tab that comes back shows current data rather than whatever it held when it was
// hidden. Everything that does not fetch the bundle is untouched: the realtime socket stays connected,
// notifications still fire from its events, and an open console keeps streaming.

/**
 * @param {object} [options]
 * @param {Document} [options.doc] the page whose visibility decides; absent (Node, a worker) means visible
 * @param {() => void} [options.onVisibleAgain] called once when a page that skipped a refresh is shown
 * @returns {{admit: () => boolean, readonly stale: boolean}}
 */
export function createRefreshGate({ doc = globalThis.document, onVisibleAgain = () => {} } = {}) {
  let stale = false;
  // ONLY `hidden` holds a refresh back. A document with no visibility state at all is treated as seen,
  // so a missing API can never leave a dashboard that silently stops updating.
  const hidden = () => Boolean(doc) && doc.visibilityState === 'hidden';

  if (doc && typeof doc.addEventListener === 'function') {
    doc.addEventListener('visibilitychange', () => {
      if (hidden() || !stale) return;
      stale = false;
      onVisibleAgain();
    });
  }

  return {
    /** Whether a refresh may run now. A refused one is remembered and run when the page is shown. */
    admit() {
      if (!hidden()) return true;
      stale = true;
      return false;
    },
    get stale() {
      return stale;
    },
  };
}
