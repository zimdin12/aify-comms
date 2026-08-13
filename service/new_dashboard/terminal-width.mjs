// How wide the console terminal renders — matching a REMOTE pane without losing the ability to shrink.
//
// The dashboard console mirrors a terminal that is really running somewhere else, and that remote pane can
// be wider than the browser's. When it is, the local xterm is widened past its fitted size and the
// container is marked so CSS can scroll it; when it is not, the terminal must go back to the width that
// actually fits.
//
// THE SUBTLETY IS WHICH WIDTH IS "NORMAL", and the source says so in its own comment: the comparison is
// against the pane's FITTED width (`entry.fitCols`), never the terminal's CURRENT width. `term.cols` may
// already be widened from a previous snapshot, so comparing against it means a pane that once went wide
// can never come back — it would keep measuring itself against its own widened state.
//
// OWNING THE PTY SHORT-CIRCUITS ALL OF IT. If this browser owns the pty, the remote's rendered width is not
// authoritative — we are the authority — so it resets to the fitted width and returns.
//
// EVERY RESIZE IS WRAPPED. xterm throws on a resize during teardown or before the renderer is attached, and
// a throw here would abort the snapshot-apply loop that calls it, freezing the console mid-update.
//
// Pure in the sense that matters for testing: the terminal, the container and the entry are all ARGUMENTS,
// so it can be driven with fakes and its decisions observed.

export function applyRenderedWidth(entry, term, container, data, ownsPty = false) {
  if (ownsPty) {
    const base = (entry && entry.fitCols) || term.cols;
    if (container) container.classList.remove('console-wide-mirror');
    try { if (term.cols !== base) term.resize(base, term.rows); } catch { /* xterm handles it */ }
    if (entry) { entry.widened = false; entry.renderedCols = base; }
    return;
  }
  // Compare against the pane's FITTED width (entry.fitCols), not the current term.cols —
  // term may already be widened from a prior snapshot, and we must be able to shrink back.
  const base = (entry && entry.fitCols) || term.cols;
  const rc = Number(data?.terminal?.renderedCols) || 0;
  const rr = Number(data?.terminal?.renderedRows) || term.rows;
  if (rc && rc > base) {
    try { term.resize(rc, Math.max(term.rows, rr)); } catch {}
    if (container) container.classList.add('console-wide-mirror');
    if (entry) { entry.widened = true; entry.renderedCols = rc; }
  } else {
    if (container) container.classList.remove('console-wide-mirror');
    try { if (term.cols !== base) term.resize(base, term.rows); } catch {}
    if (entry) { entry.widened = false; entry.renderedCols = base; }
  }
}
