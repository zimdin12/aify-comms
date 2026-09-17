// Input reaches the PTY IN ORDER, and a burst travels as one request.
//
// MEASURED 2026-09-17: every keystroke was its own POST, chained behind the previous one, and every
// POST became a separate terminal control for aify-env to claim. A wheel scroll over a full-screen TUI
// sends up to five arrow keys per wheel event, and wheel events fire many times a second, so the chain
// and the control queue behind it grew faster than they drained: the operator saw 3-4 s of lag and a
// busy CPU. Bytes that arrive while a POST is in flight are now appended to ONE pending body and sent
// when it returns. The PTY receives the same byte stream in the same order; it just arrives in fewer
// writes. Each call's promise settles once the request carrying its bytes has.
export const createTerminalInputPoster = ({ api, terminalId, onError = () => {} }) => {
  const post = (body) => api(`/terminals/${encodeURIComponent(terminalId)}/input`, {
    method: 'POST',
    body: JSON.stringify({ body, requestedBy: 'dashboard' }),
  }).catch(onError);
  let inFlight = null;
  let buffered = '';
  let bufferedSent = null;
  let settleBuffered = null;
  const sendNext = () => {
    if (!buffered) {
      inFlight = null;
      return;
    }
    const body = buffered;
    const settle = settleBuffered;
    buffered = '';
    bufferedSent = null;
    settleBuffered = null;
    inFlight = post(body).then(() => { settle(); sendNext(); });
  };
  return (data) => {
    if (!inFlight) {
      const sent = post(data);
      inFlight = sent.then(sendNext);
      return sent;
    }
    buffered += data;
    if (!bufferedSent) bufferedSent = new Promise((resolve) => { settleBuffered = resolve; });
    return bufferedSent;
  };
};

// Should a wheel gesture be translated into arrow keystrokes for the PTY, and if so which?
// Returns the byte sequence to send, or null to leave the wheel alone.
//
// Extracted 2026-07-27 from app.js's inline onWheel so the decision is testable. It was the source
// of an operator-visible corruption: `wheel` does not require focus, so scrolling the page with the
// pointer merely hovering over a console injected up to 5 synthetic arrow keypresses per event into
// that agent's live PTY. Inside a composer, arrows move the cursor, so an operator scrolling to read
// scattered their own subsequent typing across the draft.
//
// The gates, in order of how much they cost to get wrong:
//   * canInput   — the console is not accepting input at all (session not live).
//   * focused    — the operator has not indicated they intend to type HERE. This is the corruption
//                  guard; hover-scroll is navigation, not input.
//   * alternate  — only a full-screen TUI needs the translation. The normal buffer has real
//                  scrollback and xterm scrolls it natively, so synthesising keys there is pure
//                  noise (and would move the cursor for nothing).
//   * deltaY     — a zero/absent delta is not a scroll; never emit a keystroke for it.
export const WHEEL_MAX_LINES = 5;

export const wheelInputSequence = ({ bufferType, canInput, focused, deltaY }) => {
  if (!canInput) return null;
  if (!focused) return null;
  if (bufferType !== 'alternate') return null;
  const delta = Number(deltaY);
  if (!Number.isFinite(delta) || delta === 0) return null;
  const lines = Math.min(WHEEL_MAX_LINES, Math.max(1, Math.round(Math.abs(delta) / 40)));
  return (delta > 0 ? '\x1b[B' : '\x1b[A').repeat(lines);
};

export const createTerminalInputHandler = ({ canInput, onBlocked, postInput }) => (data) => {
  if (!canInput()) {
    onBlocked();
    return undefined;
  }
  return postInput(data);
};

export const waitForTerminalSize = async ({ cols, rows, readSize, delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) => {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const current = await readSize();
    if (Number(current?.cols) === cols && Number(current?.rows) === rows) return;
    await delay(100);
  }
  throw new Error(`Terminal resize to ${cols}x${rows} was not applied`);
};

export const forceTerminalRepaint = async ({ cols, rows, resize, waitForSize }) => {
  const width = Math.max(20, Number(cols) || 80);
  const height = Math.max(5, Number(rows) || 24);
  const nudge = width === 20 ? 21 : width - 1;

  await resize(nudge, height);
  await waitForSize(nudge, height);
  await resize(width, height);
  await waitForSize(width, height);
};
