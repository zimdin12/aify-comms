// Mounting an xterm against a live terminal — the dashboard's console, and the largest single function
// in app.js. Extracted in v0.5.4.
//
// IT BRINGS ITS OWN STATE. `_consoleMountGen` and `consoleInputBlockedToastAt` moved with it because
// this function is the only reader of either: the generation counter is how a mount parked awaiting a
// font load detects that a NEWER mount superseded it, and the toast timestamp debounces the
// input-blocked warning. Both are meaningless anywhere else.
//
// `resyncActiveConsole` is INJECTED rather than imported because it reaches `refresh`, the render
// orchestrator app.js still owns — importing it here would drag the whole render web across.
//
// The body is byte-identical to what left app.js; only the signature gained the injected parameter.

import { api } from './api-client.mjs';
import { copyText } from './clipboard.mjs';
import { agentForTerminal } from './session-rail.mjs';
import { terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';
import { state } from './state.mjs';
import { createTerminalInputHandler, createTerminalInputPoster, forceTerminalRepaint, wheelInputSequence } from './terminal-input.mjs';
import { applyRenderedWidth } from './terminal-width.mjs';
import { toast } from './ui.js';
import { awaitTerminalSize, disposeActiveXterm } from './xterm-lifecycle.mjs';

let _consoleMountGen = 0; // bumped per mount so a font-await-parked mount can detect supersession
let consoleInputBlockedToastAt = 0;

export async function mountXtermForTerminal(terminalId, agentId, container, { canInput = true } = {}, { resyncActiveConsole }) {
  if (!container || !terminalId) return;
  if (typeof window.Terminal === 'undefined') {
    // Non-xterm fallback: a scrolling text dump of the buffered output (parity with the old
    // dashboard's console-output-fallback) rather than just an error line.
    container.innerHTML = '<pre class="console-output-fallback" aria-live="polite"></pre>';
    const pre = container.querySelector('pre');
    try {
      const data = await api(`/terminals/${encodeURIComponent(terminalId)}`);
      if (pre) { pre.textContent = String(data?.terminal?.output || ''); pre.scrollTop = pre.scrollHeight; }
    } catch { if (pre) pre.textContent = '[xterm.js unavailable and history fetch failed]'; }
    state.activeXterm = { terminalId, agentId, term: null, fitAddon: null, container, fallbackPre: pre, lastSeq: -1, canInput };
    return;
  }
  if (
    state.activeXterm
    && state.activeXterm.terminalId === terminalId
    && state.activeXterm.container === container
    && container.isConnected !== false
  ) {
    state.activeXterm.canInput = canInput;
    return;
  }
  disposeActiveXterm();
  container.innerHTML = '';
  // Mount generation: state.activeXterm is null from here until we assign it below, and the font
  // warm-up awaits in between. A rapid session switch can start a newer mount during that gap; this
  // token lets the older (superseded) mount bail before it creates a WebGL context / claims
  // state.activeXterm — otherwise it leaks an xterm + GL context nothing will dispose.
  const _mountGen = ++_consoleMountGen;

  const term = new window.Terminal({
    // This is a real PTY byte stream. Rewriting LF to CRLF changes cursor semantics and is one
    // reason this mirror diverged from Hermes' direct xterm attachment.
    convertEol: false,
    cursorBlink: true,
    fontFamily: '"Cascadia Code", ui-monospace, "Consolas", monospace',
    fontSize: 13,
    theme: terminalThemeFromDashboard(),
    scrollback: 5000,
    // Hermes terminal-setup parity (studied from their dashboard ChatPage + desktop shell):
    //  - allowProposedApi: REQUIRED for the Unicode11 addon we activate below (without it xterm
    //    warns and the width provider silently stays on the core tables → CJK/emoji misalign).
    //  - minimumContrastRatio: xterm's default is 1 (OFF), which paints raw saturated ANSI —
    //    dark-blue-on-black is unreadable. 4.5:1 (WCAG AA) is Hermes' "VS Code secret sauce":
    //    it clamps fg against bg at render time so low-contrast ANSI stays legible.
    //  - selection ergonomics: force native selection under mouse-tracking TUIs and select-word
    //    on right-click, matching their gnome-terminal-parity behavior.
    allowProposedApi: true,
    minimumContrastRatio: 4.5,
    macOptionClickForcesSelection: true,
    rightClickSelectsWord: true,
  });
  let fitAddon = null;
  if (window.FitAddon && window.FitAddon.FitAddon) {
    fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(fitAddon);
  }
  // Guarded fit (Hermes parity). Never run fit() on a detached or zero-sized host: fit()
  // triggers a WebGL texture-atlas rebuild, and doing that while a sibling pane is mid-transition
  // at 0px crashes the GL renderer (their comment, learned the hard way). Guard on connected +
  // measurable box so a hidden/collapsing pane simply skips the fit and re-fits when visible.
  const safeFit = () => {
    if (!fitAddon || !container.isConnected) return;
    if (container.clientWidth <= 0 || container.clientHeight <= 0) return;
    try { fitAddon.fit(); } catch {}
  };
  // Match Hermes dashboard's terminal fidelity: Unicode 11 supplies current wide-character
  // cell widths (important for Ink/TUI cursor alignment) and web-links makes rendered URLs
  // clickable without changing the underlying PTY bytes.
  if (window.Unicode11Addon && window.Unicode11Addon.Unicode11Addon) {
    try {
      term.loadAddon(new window.Unicode11Addon.Unicode11Addon());
      term.unicode.activeVersion = '11';
    } catch { /* core Unicode provider remains active */ }
  }
  if (window.WebLinksAddon && window.WebLinksAddon.WebLinksAddon) {
    try { term.loadAddon(new window.WebLinksAddon.WebLinksAddon()); } catch {}
  }
  // Font warm-up before first open/fit (Hermes parity). fit() converts the pane's pixel box into
  // cols/rows using the FONT's cell metrics. If the terminal font hasn't loaded yet, fit measures
  // FALLBACK metrics → wrong row count → the shell boots at the wrong size → an extra SIGWINCH and
  // a stretch of stale/blank rows. Worse, the WebGL renderer would bake the fallback face into its
  // glyph atlas. Wait for the weights we render (regular/bold/italic) before opening. allSettled +
  // a font the host lacks (Cascadia Code absent → Consolas fallback) simply resolves empty — no-op.
  if (document.fonts && typeof document.fonts.load === 'function') {
    try {
      await Promise.allSettled([
        document.fonts.load('13px "Cascadia Code"'),
        document.fonts.load('bold 13px "Cascadia Code"'),
        document.fonts.load('italic 13px "Cascadia Code"'),
      ]);
    } catch { /* fonts API hiccup: proceed; fit re-runs on the ResizeObserver anyway */ }
  }
  // Superseded during the font await (or the pane was detached)? Drop THIS term before opening it /
  // creating a GL context / claiming state.activeXterm, so a newer mount is the only live console.
  if (_mountGen !== _consoleMountGen || !container.isConnected) {
    try { term.dispose(); } catch {}
    return;
  }
  term.open(container);
  // WebGL renderer (WS-D) — big perf win under heavy TUI output; fall back to the DOM
  // renderer if the GL context is lost or the addon throws. Kept referenceable so a live theme
  // change can clear its glyph-color texture atlas (refreshActiveTerminalTheme).
  let webglAddon = null;
  if (window.WebglAddon && window.WebglAddon.WebglAddon) {
    try {
      const webgl = new window.WebglAddon.WebglAddon();
      webgl.onContextLoss(() => { try { webgl.dispose(); } catch {} webglAddon = null; });
      term.loadAddon(webgl);
      webglAddon = webgl;
    } catch { /* DOM renderer remains active */ }
  }
  safeFit();

  // Keystroke forwarding back to the bridge PTY via /terminals/<id>/input.
  // Service request shape (TerminalControlRequest in api_v2.py): {body, requestedBy}.
  // Hermes uses one ordered WebSocket. We still cross the service API, so serialize requests:
  // parallel fetches can otherwise deliver consecutive keystroke chunks out of order.
  const postTerminalInput = createTerminalInputPoster({
    api,
    terminalId,
    onError: (err) => {
      term.write(`\r\n\x1b[31m[input post failed: ${String(err?.message || err).replace(/\x1b/g, '')}]\x1b[0m\r\n`);
    },
  });
  term.onData(createTerminalInputHandler({
    canInput: () => !(state.activeXterm && state.activeXterm.canInput === false),
    onBlocked: () => {
      const now = Date.now();
      if (now - consoleInputBlockedToastAt > 4000) {
        consoleInputBlockedToastAt = now;
        toast('This console is not accepting input right now (session not live).', 'warn');
      }
    },
    postInput: postTerminalInput,
  }));
  // Emit-resize-only-on-change (hermes parity): xterm fires onResize on every fit even when the
  // grid dims didn't actually change — debounce AND dedupe so we don't spam the PTY with no-ops.
  let resizeTimer = 0;
  let lastCols = 0;
  let lastRows = 0;
  term.onResize(({ cols, rows }) => {
    const c = Math.max(20, cols);
    const r = Math.max(5, rows);
    if (c === lastCols && r === lastRows) return;
    lastCols = c; lastRows = r;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      api(`/terminals/${encodeURIComponent(terminalId)}/resize`, {
        method: 'POST',
        body: JSON.stringify({ cols: c, rows: r, requestedBy: 'dashboard' }),
      }).catch(() => {});
    }, 120);
  });

  // OSC-52 clipboard (hermes parity): honor programs inside the PTY that emit the OSC 52 "set
  // clipboard" sequence (vim "+y, tmux, etc.) by copying to the browser clipboard — works on the
  // http loopback origin via the execCommand fallback in copyText().
  try {
    term.parser.registerOscHandler(52, (data) => {
      const payload = String(data || '');
      const b64 = payload.slice(payload.indexOf(';') + 1);
      if (!b64 || b64 === '?') return true;
      try { copyText(atob(b64)); } catch { /* malformed base64 — ignore */ }
      return true;
    });
  } catch { /* older xterm without parser.registerOscHandler */ }

  // Copy/paste key handler for the http LAN origin (navigator.clipboard is undefined there):
  // Ctrl+Shift+C copies the selection, Ctrl+Shift+V / Ctrl+V pastes via the clipboard API when
  // available (loopback secure context) and otherwise leaves the keystroke to flow to the PTY.
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== 'keydown') return true;
    if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
      if (term.hasSelection()) { copyText(term.getSelection()); return false; }
    }
    if ((e.ctrlKey && e.shiftKey && (e.key === 'V' || e.key === 'v')) || (e.ctrlKey && !e.shiftKey && (e.key === 'V' || e.key === 'v'))) {
      if (navigator.clipboard?.readText) {
        navigator.clipboard.readText().then((txt) => { if (txt) term.paste(txt); }).catch(() => {});
        return false;
      }
    }
    return true;
  });

  // Wheel → arrow keys when a full-screen TUI owns the alternate screen buffer (claude/hermes Ink
  // UIs): a raw wheel does nothing inside the alt-screen, so translate it to cursor up/down so the
  // operator can scroll the agent's UI with the mouse like the old dashboard allowed.
  //
  // TWO FIXES, 2026-07-27, from an operator report of "I try to write and delete stuff in the
  // dashboard terminal but I can't" — a composer full of scrambled escape-sequence fragments.
  //
  // 1. It POSTED DIRECTLY, bypassing `postTerminalInput`. The comment 70 lines above this one
  //    explains exactly why that is wrong — "serialize requests: parallel fetches can otherwise
  //    deliver consecutive keystroke chunks out of order" — and then this handler opened a second,
  //    UNORDERED writer to the same PTY. A wheel gesture emits a burst of events, each firing its
  //    own fetch, so wheel arrows and real keystrokes interleaved arbitrarily. Now routed through
  //    the same serialized queue, so there is ONE ordered writer per console.
  //
  // 2. It fired on HOVER. `wheel` does not require focus, so merely scrolling the page with the
  //    pointer over a console injected up to 5 synthetic arrow keypresses PER EVENT into that
  //    agent's live PTY. Inside a composer, arrows move the cursor — so an operator scrolling to
  //    read scattered their own subsequent typing across the draft, which is precisely the reported
  //    symptom. Keystroke injection now requires the terminal to actually HAVE FOCUS, which is the
  //    honest signal for "I intend to type here". Hover-scroll is navigation, not input.
  //
  // Deliberately NOT filtering what xterm emits from real keys/mouse — that is the raw-passthrough
  // contract (`server.js`: "Raw passthrough: callers own newline semantics"). This only stops the
  // dashboard SYNTHESISING input the operator never typed.
  const onWheel = (ev) => {
    try {
      // Focus gate: `document.activeElement` is xterm's hidden textarea when the terminal is
      // focused. Without it, a wheel over an unfocused pane types into someone's draft.
      const seq = wheelInputSequence({
        bufferType: term.buffer?.active?.type,
        canInput: state.activeXterm?.canInput !== false,
        focused: !!(term.textarea && document.activeElement === term.textarea),
        deltaY: ev.deltaY,
      });
      if (!seq) return; // let the page scroll; do not inject keystrokes
      postTerminalInput(seq);
      ev.preventDefault();
    } catch { /* leave native behavior */ }
  };
  try { container.addEventListener('wheel', onWheel, { passive: false }); } catch {}

  // Re-fit on container/window resize so the terminal tracks the pane size.
  let resizeObserver = null;
  if (window.ResizeObserver && fitAddon) {
    let resyncTimer = null;
    // Coalesce observer bursts to a SINGLE rAF (Hermes parity). A ResizeObserver fires many times
    // during a layout transition; running fit() synchronously on each — especially through a 0px
    // frame — is what crashes the WebGL atlas. One rAF per burst also lets the box settle before
    // we measure. `roFrame` guards against stacking frames; safeFit() no-ops on a 0/detached box.
    let roFrame = 0;
    resizeObserver = new ResizeObserver(() => {
      if (roFrame) return;
      roFrame = requestAnimationFrame(() => {
        roFrame = 0;
        const entry = state.activeXterm;
        // Stale-observer guard: this frame may have been scheduled just before dispose. If the
        // active console is no longer THIS container's, bail — otherwise a disposed terminal's
        // observer would mutate the new entry (spurious resync/flicker).
        if (!entry || entry.container !== container) return;
        // Wide mirror (resident terminal wider than the pane): fit() would shrink the xterm back
        // to the pane and re-wrap the source lines. Instead recompute the pane width WITHOUT
        // applying it; only if it changed materially do we resync (which re-fits + re-widens).
        if (entry && entry.widened) {
          let paneCols = entry.fitCols || 0;
          try { const d = fitAddon.proposeDimensions && fitAddon.proposeDimensions(); if (d && d.cols) paneCols = d.cols; } catch {}
          if (paneCols && Math.abs(paneCols - (entry.fitCols || 0)) >= 2) {
            entry.fitCols = paneCols;
            clearTimeout(resyncTimer);
            resyncTimer = setTimeout(() => { resyncActiveConsole(); }, 220);
          }
          return;
        }
        safeFit();
        // The snapshot was server-rendered at a fixed column count. If a late layout settle (page
        // switch / flex-fill) changes the column count after that, the rendered snapshot is now the
        // wrong width ("narrow and bugged"). Re-fetch + repaint at the new size, debounced, so the
        // console self-heals instead of staying stuck at the mount-time width.
        if (entry && entry.term && entry.term.cols !== entry.renderedCols) {
          entry.renderedCols = entry.term.cols;
          entry.fitCols = entry.term.cols;
          clearTimeout(resyncTimer);
          resyncTimer = setTimeout(() => { resyncActiveConsole(); }, 220);
        }
      });
    });
    try { resizeObserver.observe(container); } catch {}
  }

  state.activeXterm = { terminalId, agentId, term, fitAddon, container, resizeObserver, wheelHandler: onWheel, lastSeq: -1, canInput, webgl: webglAddon, _themeAccent: terminalAccentColor() };

  // Replay existing buffered output so the operator sees history when they open the Console
  // pane mid-session (instead of waiting for the next byte to arrive).
  // Fit FIRST (next frame, after layout settles + with min-width:0 ancestors so fit() measures
  // the VISIBLE pane, not an overflowing one), THEN fetch the snapshot at the settled cols/rows.
  // Fetching before the fit settled rendered the snapshot too wide ("tries to compensate").
  // Double rAF: one frame to apply layout, a second so the flex-fill width is final before fit()
  // measures cols (a single frame can still read a transient narrow width on a fresh page switch).
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  safeFit();
  try {
    // Pass our (settled) grid size so the server renders a CLEAN current-screen snapshot (via the
    // headless VT emulator) instead of the raw byte log — replaying the raw log scrambles
    // full-screen TUIs. Prefer `snapshot`; fall back to raw `output` (e.g. pyte absent).
    const cols = Math.max(20, term.cols || 80), rows = Math.max(5, term.rows || 24);
    if (state.activeXterm) { state.activeXterm.renderedCols = term.cols; state.activeXterm.fitCols = term.cols; }
    const data = await api(`/terminals/${encodeURIComponent(terminalId)}?cols=${cols}&rows=${rows}`);
    // We OWN a managed PTY: fit it to the pane instead of stretching the pane to it. Only a
    // RESIDENT console is a mirror of a terminal we must not resize.
    //
    // Own the PTY ONLY when POSITIVELY managed (2026-07-19). Unknown / missing-agent / empty-mode
    // must fall through to false → we do NOT resize (a resident console mirrors the operator's real
    // terminal; SIGWINCHing it is the exact harm this guard prevents). The old `!== 'resident'`
    // failed OPEN: a not-yet-populated state.agents made an unknown mode read as owned. Fall back to
    // the session row's own mode so an absent agent object can't flip a resident console to "owned".
    //
    // THE SESSION-ROW FALLBACK THIS COMMENT DESCRIBES HAS NEVER RUN. It read
    // `_sess?.sessionMode || _sess?.session_mode`, and /sessions emits neither -- the session row
    // carries `mode` ('managed-warm') and `ownerMode` ('managed' / 'resident' / 'console'). So an
    // absent agent object has always produced an empty `_mode`, which is `ownsPty === false`: the
    // fail-closed direction the paragraph above demands. Nothing is broken.
    //
    // MAKING IT REAL IS A DECISION, NOT A REPAIR. Substituting `_sess?.ownerMode` would let this
    // guard answer `managed` where it answers nothing today, so a console that is currently never
    // resized could start being resized. That is the exact harm the guard exists to prevent, and it
    // is not a change to make while removing dead reads. Left fail-closed, and stated.
    const _mode = String(agentForTerminal(terminalId)?.sessionMode || '').toLowerCase();
    const ownsPty = _mode === 'managed';
    applyRenderedWidth(state.activeXterm, term, container, data, ownsPty);
    if (state.activeXterm) state.activeXterm.ownsPty = ownsPty;

    // Force one real width transition on a PTY we own — do not wait for xterm's onResize.
    //
    // This is what actually un-garbles a console, and it took a browser to see it. These TUIs
    // paint by ABSOLUTE cursor position and never scroll (measured: zero newlines, 1160 CUP moves
    // per 64KB), and they repaint only what CHANGED. So a screen we started tracking mid-stream
    // keeps its wrong rows FOREVER — the operator's "gibberish", with two lines woven together
    // character by character. Nothing we do server-side can fix it, because the app will never
    // redraw those rows on its own.
    //
    // A genuine RESIZE does force a full repaint (verified live: the app emitted 23 chunks and
    // the screen came back clean). But `term.onResize` only fires when xterm's own size CHANGES,
    // and Linux sends no SIGWINCH for a same-size resize. Nudge one column and restore it before
    // pulling the freshly-repainted snapshot.
    if (ownsPty) {
      const c = Math.max(20, term.cols), r2 = Math.max(5, term.rows);
      try {
        await forceTerminalRepaint({
          cols: c,
          rows: r2,
          resize: (nextCols, nextRows) => api(`/terminals/${encodeURIComponent(terminalId)}/resize`, {
            method: 'POST',
            body: JSON.stringify({ cols: nextCols, rows: nextRows, requestedBy: 'dashboard-attach' }),
          }),
          waitForSize: (nextCols, nextRows) => awaitTerminalSize(terminalId, nextCols, nextRows),
        });
        await new Promise((res) => setTimeout(res, 700));   // let the app repaint
        const fresh = await api(`/terminals/${encodeURIComponent(terminalId)}?cols=${c}&rows=${r2}`);
        if (fresh?.terminal?.snapshot) data.terminal = fresh.terminal;
      } catch { /* best-effort: fall back to the snapshot we already have */ }
    }
    const snapshot = data?.terminal?.snapshot;
    const output = data?.terminal?.output;
    // reset() BEFORE seeding, exactly as the Refresh path does. The snapshot's own prefix only
    // clears the visible screen (ESC[2J) — it does not reset scrollback, charset, scroll region
    // or alt-screen state, so writing it into a REUSED xterm can leave stale rows and a stuck
    // line-drawing charset underneath ("____ everywhere"). A full reset makes the seed
    // self-contained no matter what the pane was showing before.
    try { term.reset(); } catch { /* xterm always has reset(); never block the seed */ }
    if (snapshot) term.write(String(snapshot));
    else if (output) term.write(String(output));
    // GET /terminals/{id} returns the buffer sequence as `outputSeq` (only the WS frame uses `seq`).
    // Reading `seq` here left lastSeq=-1, disabling dedup so the first live frames re-painted history.
    if (state.activeXterm) state.activeXterm.lastSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? state.activeXterm.lastSeq);
  } catch (err) {
    term.write(`\r\n\x1b[2m[history fetch failed: ${String(err?.message || err).replace(/\x1b/g, '')}]\x1b[0m\r\n`);
  }
  term.focus();
}
