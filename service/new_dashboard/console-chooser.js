// Pure Session-Console widget chooser for the Dashboard Next SPA. This is the hardest,
// most bug-encoding subsystem of the dashboard — it picks which console widget to mount
// (live xterm PTY / hermes gateway iframe / codex synth console / none) and the effective
// terminalId, and it must NOT oscillate when the server transiently clears
// runtime_state.virtualTerminalId. Kept pure (everything in via params) so it unit-tests
// without a DOM — see console-chooser.test.mjs. (DASHBOARD_REBUILD_PLAN §5.1.)

// Convert a hermes gateway ws(s):// URL into an embeddable http(s):// origin, but ONLY for
// loopback hosts — embedding a public host would leak the token through the iframe URL.
export function hermesGatewayUrlToHttp(wsUrl) {
  const raw = String(wsUrl || '').trim();
  if (!/^wss?:\/\//i.test(raw)) return '';
  try {
    const u = new URL(raw);
    if (!['127.0.0.1', 'localhost', '::1'].includes(u.hostname)) return '';
    const scheme = u.protocol === 'wss:' ? 'https' : 'http';
    const token = u.searchParams.get('token') || '';
    const query = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${scheme}://${u.hostname}:${u.port || (scheme === 'https' ? '443' : '80')}/${query}`;
  } catch (_) {
    return '';
  }
}

// Pick the Session Console widget and the effective terminalId to use. Caches the most-recent
// terminalId per session so the widget doesn't oscillate when the server temporarily clears
// runtime_state.virtualTerminalId (Bug #3: _stop_virtual_terminals_for_superseded_bridges runs
// on every dashboard list-sessions refresh and wipes virtualTerminalId; the terminal_session
// row itself usually still exists, so we keep showing the xterm widget mounted to the cached
// terminalId until the operator switches sessions). Stateless contract so it can be
// unit-tested without DOM setup.
//
// Inputs: { agent, sessionId, sessionMode, terminalStatus, runtime, runtimeConfig,
//           cache: Map<sessionId, terminalId>, hermesGatewayHttp, codexAppServerUrl,
//           codexThreadId, codexAttachable }
// Output: { kind: 'xterm' | 'hermes-iframe' | 'codex-synth' | 'none', terminalId, isLive,
//           hermesGatewayHttp, codexAppServerUrl, codexThreadId }
//
// Plan 4 Task 18: prefer the wrapper PTY (runtimeState.terminalId) over the synth virtual-rpc
// terminal (runtimeState.virtualTerminalId) — the wrapper PTY is the real operator-facing Ink
// TUI; the synth is the lower-fidelity JSON-RPC shim.
export function chooseSessionConsoleWidget({ agent, sessionId, sessionMode, sessionStatus, terminalStatus, runtime, runtimeConfig, cache, hermesGatewayHttp, codexAppServerUrl, codexThreadId, codexAttachable, sessionTerminalId }) {
  const normalizedSessionMode = String(sessionMode || agent?.sessionMode || agent?.session_mode || '').trim().toLowerCase();
  const normalizedTerminalStatus = String(terminalStatus || agent?.terminalStatus || agent?.terminal_status || '').trim().toLowerCase();
  // A DEAD session with no explicit live terminal status must not mount an xterm from
  // leftover runtime_state pointers (graph-tech-lead incident 2026-07-02: rs.terminalId
  // still named a terminal stopped a day earlier — the dashboard rendered its stale 65KB
  // buffer as if live, dead input, and never offered Start). An explicitly-live
  // terminalStatus (attached/active/starting) still wins — a console freshly started on
  // a previously-stopped session mounts as before.
  const sessionDead = ['stopped', 'failed', 'lost', 'ended', 'completed', 'cancelled']
    .includes(String(sessionStatus || '').trim().toLowerCase());
  const terminalCanRepresentCurrentOwner = normalizedSessionMode !== 'resident'
    && !['stopping', 'stopped', 'failed'].includes(normalizedTerminalStatus)
    && !(sessionDead && !normalizedTerminalStatus);
  // AUTO-ATTACH FIX (2026-06-19): a terminal that went live via dispatch/register/bind lands in
  // runtime_state.consoleTerminal.terminalId (nested) or agent_sessions.terminal_id (session-bound),
  // NOT the top-level runtime_state.terminalId (only the dashboard-start path writes that). Reading
  // only the top-level field made live terminals fall through to the "Start console" offer. Honor
  // all real sources (wrapper PTY first, then the console pointer, then synth, then session-bound)
  // so a running terminal AUTO-MOUNTS and Start is only offered when genuinely none exists.
  const rs = agent?.runtimeState || {};
  const liveTerminalId = terminalCanRepresentCurrentOwner
    ? String(rs.terminalId || rs.consoleTerminal?.terminalId || rs.virtualTerminalId || sessionTerminalId || '').trim()
    : '';
  if (liveTerminalId && cache && typeof cache.set === 'function') {
    cache.set(String(sessionId || ''), liveTerminalId);
  }
  const cachedTerminalId = (cache && typeof cache.get === 'function')
    ? String(cache.get(String(sessionId || '')) || '').trim()
    : '';
  const effectiveTerminalId = terminalCanRepresentCurrentOwner ? (liveTerminalId || cachedTerminalId) : '';

  if (effectiveTerminalId) {
    return {
      kind: 'xterm',
      terminalId: effectiveTerminalId,
      isLive: Boolean(liveTerminalId),
      hermesGatewayHttp: '',
      codexAppServerUrl: '',
      codexThreadId: '',
    };
  }
  // Hermes (resident OR managed) with no PTY terminal: embed the loopback gateway INLINE so the
  // TUI shows IN our dashboard rather than forcing an "Open in new tab" out to the hermes web UI.
  // Managed hermes runs in the tui_gateway (no node-pty wrapper to xterm), so the gateway iframe is
  // its real in-dashboard surface. Only reached when there's no effectiveTerminalId (xterm wins
  // above), and hermesGatewayHttp is loopback-only (hermesGatewayUrlToHttp gates non-loopback).
  if (runtime === 'hermes' && hermesGatewayHttp) {
    return {
      kind: 'hermes-iframe',
      terminalId: '',
      isLive: false,
      hermesGatewayHttp,
      codexAppServerUrl: '',
      codexThreadId: '',
    };
  }
  if (normalizedSessionMode === 'resident' && runtime === 'codex' && codexAttachable) {
    return {
      kind: 'codex-synth',
      terminalId: '',
      isLive: false,
      hermesGatewayHttp: '',
      codexAppServerUrl,
      codexThreadId,
    };
  }
  return { kind: 'none', terminalId: '', isLive: false, hermesGatewayHttp: '', codexAppServerUrl: '', codexThreadId: '' };
}
