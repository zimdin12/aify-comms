// The dashboard's realtime socket, and everything that owns its state.
//
// This cluster left app.js in v0.5.4 as ONE unit rather than function by function, because its four
// mutable names — the socket, the backoff counter, the resume-nudge stamp — have no reader outside
// it. Splitting the functions from the state would have produced a module that reaches back into
// app.js for a variable, which is the shape this series exists to remove.
//
// The five injected names are app.js's render orchestrator and its neighbours. `apiOrigin` is NOT
// among them: api-client.mjs already exports it as a live binding kept current by `setApiBase`, so
// it is imported like any other module value.
//
// INIT IS EXPLICIT AND MUST HAPPEN BEFORE `connectRealtimeSocket`. The alternative — passing the bag
// to each function — cannot work here: `sock.onclose` schedules `setTimeout(connectRealtimeSocket,
// delay)`, a self-call with no bag to pass, so the first reconnect after a deploy would have run
// with undefined dependencies. That path only executes when the service restarts, which is exactly
// when nobody is watching.

import { apiOrigin } from './api-client.mjs';
import { state } from './state.mjs';
import { updateAwaitPill } from './console-await.mjs';

let dashboardNotifier = { handle() {} };
let evaluateFlowGates = () => {};
let refreshSoon = () => {};
let resyncActiveConsole = async () => {};
let scheduleRenderAll = () => {};

/** Supply the app.js-side dependencies. Throws rather than silently accepting a partial bag. */
export function initRealtimeSocket(deps) {
  const REQUIRED = ['dashboardNotifier', 'evaluateFlowGates', 'refreshSoon', 'resyncActiveConsole', 'scheduleRenderAll'];
  const missing = REQUIRED.filter((k) => deps == null || deps[k] == null);
  if (missing.length) throw new TypeError(`initRealtimeSocket requires ${missing.join(', ')}`);
  ({ dashboardNotifier, evaluateFlowGates, refreshSoon, resyncActiveConsole, scheduleRenderAll } = deps);
  // Re-initialising means starting over, so any socket from a previous init is CLOSED, not abandoned.
  // app.js calls this once, before the first connect, where there is nothing to close. It matters for
  // the suite, which inits per test: without it the CONNECTING guard in `connectRealtimeSocket` sees a
  // socket left behind by an earlier test and silently declines to connect.
  if (dashboardSocket) { try { dashboardSocket.close(); } catch { /* already gone */ } }
  dashboardSocket = null;
  _wsReconnectAttempts = 0;
  _wsResumeNudgeAt = 0;
}

let dashboardSocket = null;

let _wsReconnectAttempts = 0;
const WS_CONNECTING_TIMEOUT_MS = 8000;
export function connectRealtimeSocket() {
  if (dashboardSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(dashboardSocket.readyState)) return;
  const wsOrigin = apiOrigin.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
  try {
    dashboardSocket = new WebSocket(`${wsOrigin}/ws`);
  } catch {
    state.realtimeConnected = false;
    return;
  }
  const sock = dashboardSocket;
  // Half-open-socket watchdog (Hermes parity, their NS-591). After a laptop sleep or a mobile
  // radio handoff a socket can sit in CONNECTING forever — neither onopen nor onclose ever fires,
  // so the CONNECTING guard above wedges reconnect permanently. If THIS socket is still CONNECTING
  // after the timeout, force-close it so onclose → backoff reconnect can recover. The timer is
  // scoped PER SOCKET (via `sock` + a local id): a shared global id could be cleared by a different
  // socket's onclose during a resume-overlap and leave a half-open successor unwatched.
  const watchdog = setTimeout(() => {
    if (sock.readyState === WebSocket.CONNECTING) { try { sock.close(); } catch {} }
  }, WS_CONNECTING_TIMEOUT_MS);
  sock.onopen = () => {
    clearTimeout(watchdog);
    const wasReconnect = state.realtimeConnected === false && _wsReconnectAttempts > 0;
    state.realtimeConnected = true;
    _wsReconnectAttempts = 0; // healthy connection → reset backoff to fast retry
    evaluateFlowGates();
    // After a dropped-then-reconnected WS (deploy, network blip, laptop sleep), any live
    // terminal_output frames emitted during the outage were missed — an IDLE agent emits no
    // new frame to trip the sequence-gap resync, so the mounted console shows STALE canvas
    // and typed keystrokes echo into a frame the tab never repaints ("can't write into the
    // terminal"). Re-sync the mounted console on reconnect so it repaints the authoritative
    // buffer immediately. Also pull fresh roster/session data.
    if (wasReconnect) {
      if (state.activeXterm && state.activeXterm.term) resyncActiveConsole().catch(() => {});
      refreshSoon();
    }
  };
  sock.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data || '{}');
      applyRealtimeEvent(payload.event, payload.data || {});
    } catch {}
  };
  sock.onclose = () => {
    clearTimeout(watchdog);
    state.realtimeConnected = false;
    // Exponential backoff (capped) instead of hammering /ws every 2.5s. The single-worker
    // service restarts on every deploy; a flat retry from every open tab piles load on exactly
    // when it's weakest. Reset to fast on a successful open (see onopen below).
    _wsReconnectAttempts = Math.min(_wsReconnectAttempts + 1, 6);
    const delay = Math.min(30000, 1500 * 2 ** _wsReconnectAttempts);
    setTimeout(connectRealtimeSocket, delay);
  };
}

// Reconnect on page-resume (Hermes parity). When a backgrounded/slept tab wakes, its socket is
// often CLOSED with a long backoff timer still pending (up to 30s away) — the operator stares at a
// stale console. On any resume signal, if we're not OPEN, reconnect NOW (short-circuiting the
// backoff). A stuck-CONNECTING socket is force-closed first so the CONNECTING guard can't block the
// fresh connect. Throttled so a burst of resume events (focus+visibilitychange+online together)
// fires one reconnect.
let _wsResumeNudgeAt = 0;
export function nudgeRealtimeSocketOnResume() {
  const now = Date.now();
  if (now - _wsResumeNudgeAt < 1000) return;
  _wsResumeNudgeAt = now;
  const rs = dashboardSocket ? dashboardSocket.readyState : WebSocket.CLOSED;
  // OPEN → nothing to do. CONNECTING → leave it: it's either progressing (aborting a healthy slow
  // connect just churns) or genuinely stuck, in which case the per-socket watchdog kills it within
  // 8s. Only a CLOSED/CLOSING socket needs an immediate reconnect (short-circuiting the backoff).
  if (rs === WebSocket.OPEN || rs === WebSocket.CONNECTING) return;
  connectRealtimeSocket();
}
export function wireRealtimeResumeReconnect() {
  const onResume = (ev) => {
    if (ev && ev.type === 'visibilitychange' && document.visibilityState !== 'visible') return;
    nudgeRealtimeSocketOnResume();
  };
  for (const [target, ev] of [[document, 'visibilitychange'], [window, 'pageshow'], [window, 'focus'], [window, 'online']]) {
    try { target.addEventListener(ev, onResume); } catch {}
  }
}

export function applyRealtimeEvent(event, data = {}) {
  // Fire-and-forget, and deliberately BEFORE the routing below: a notification must never depend
  // on which branch the event takes, and must never be able to break the dashboard's own handling
  // of it. The notifier swallows its own errors and returns a reason string.
  try { dashboardNotifier.handle(event, data); } catch {}
  if (event === 'terminal_started' && data.terminalId && data.agentId) {
    state.terminalOwners.set(String(data.terminalId), String(data.agentId));
    refreshSoon();
    return;
  }
  if (event === 'terminal_output' && data.terminalId) {
    const owner = state.terminalOwners.get(String(data.terminalId));
    if (owner && data.agentId && data.agentId !== owner) return;
    if (data.agentId) state.terminalOwners.set(String(data.terminalId), String(data.agentId));
    // Live PTY rendering: if this terminal is currently mounted in the Session Console pane,
    // write the new bytes straight to the xterm.js instance — no DOM refresh for the stream.
    const entry = state.activeXterm;
    // Skip painting when the console pane is hidden (operator switched pages): the xterm stays
    // mounted but offscreen, so writing to it just burns CPU and grows scrollback invisibly.
    // It re-syncs from the authoritative buffer on next mount/visible render.
    if (entry && entry.container && entry.container.offsetParent === null) return;
    if (entry && String(entry.terminalId) === String(data.terminalId) && data.output) {
      // Seq-based dedup + gap-resync (WS-D): the server tags frames with a monotonic seq.
      // Drop frames we've already painted; on a gap (missed a frame, e.g. WS reconnect blip)
      // re-fetch the authoritative buffer instead of painting out-of-order bytes.
      const seq = Number(data.seq);
      if (Number.isFinite(seq) && entry.lastSeq >= 0) {
        if (seq <= entry.lastSeq) { return; }
        if (seq > entry.lastSeq + 1) { resyncActiveConsole().catch(() => {}); return; }
      }
      if (Number.isFinite(seq)) entry.lastSeq = seq;
      try {
        if (entry.term) entry.term.write(data.output);
        else if (entry.fallbackPre) { entry.fallbackPre.textContent += data.output; entry.fallbackPre.scrollTop = entry.fallbackPre.scrollHeight; }
        entry.recentText = (String(entry.recentText || '') + String(data.output)).slice(-600);
        updateAwaitPill();
      } catch {}
    }
    // NOTE: do NOT refreshSoon() here. terminal_output streams every 1-4s; a full data
    // refetch per frame made the api-status chip flap 'refreshing'↔'live' every second and
    // wasted the 9-endpoint refetch. Live bytes are written to xterm above; agent/roster data
    // changes arrive via the granular agent_status / other WS events below.
    return;
  }
  // Granular consumption (Phase 1.2): a status change patches the agent in place and
  // re-renders (signature-gated) WITHOUT the 9-endpoint full refetch — the dashboard's
  // biggest poll-load reduction. Only fall back to refreshSoon for events that change data
  // the client can't synthesize from the event payload.
  if (event === 'agent_status' && data.agentId) {
    const agent = state.agents.find((a) => a.id === data.agentId);
    if (agent) {
      if (data.status) { agent.status = data.status; agent.statusRaw = data.status; }
      if (data.statusNote !== undefined) agent.statusNote = data.statusNote;
      scheduleRenderAll();
      return;
    }
    refreshSoon(); // unknown agent — a registration we haven't loaded yet
    return;
  }
  if ([
    'message_sent',
    'dispatch_queued',
    'dispatch_claimed',
    'dispatch_updated',
    'dispatch_control_requested',
    'dispatch_control_updated',
    'contract_reminders_sent',
    'settings_updated',
    'session_control_requested',
    'session_deleted',
    'agent_registered',
  ].includes(event)) {
    refreshSoon();
  }
}
