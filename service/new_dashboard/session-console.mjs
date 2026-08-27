// Rendering the session console pane. Extracted from app.js in v0.5.4.
//
// It is the surface an operator watches a managed agent through, and it decides between a real PTY, a
// synthesized RPC terminal and a plain transcript — then hands off to the xterm mount. 236 lines.
//
// THREE DEPENDENCIES ARE INJECTED, all for the same reason: each reaches `refresh`, the render
// orchestrator app.js still owns. Importing any of them here would pull the whole render web across,
// which is exactly what kept this function in place until the mount moved out ahead of it.

import { api } from './api-client.mjs';
import { codexConsoleClose } from './codex-console.mjs';
import { fillDeadConsoleCause } from './dead-console-cause.mjs';
import { chooseSessionConsoleWidget, hermesGatewayUrlToHttp } from './console-chooser.js';
import { sessionAgentId, sessionEnvironmentId, sessionId, sessionRuntime } from './record-fields.mjs';
import { agentForSession, renderModeSwitchChip, renderSessionModeLabel } from './session-rail.mjs';
import { state } from './state.mjs';
import { byId } from './ui.js';
import { esc } from './util.js';
import { disposeActiveXterm } from './xterm-lifecycle.mjs';

export function renderSessionConsole(session, targetEl, opts = {}, { mountXtermForTerminal, refresh, resyncActiveConsole } = {}) {
  const host = targetEl || byId('session-console-summary');
  if (!host) return;
  // Dual-host xterm guard (2026-06-19 review): this runs for BOTH the Sessions summary host
  // AND the Chat inline console host, and renderSessionWorkspace() calls it on EVERY poll
  // regardless of the active page. state.activeXterm is a single global, so a HIDDEN host
  // re-rendering would dispose+re-mount the live xterm out from under the VISIBLE host → the
  // other pane goes black ("visible for a sec then black", now reachable cross-host since
  // auto-attach mounts terminals for far more sessions). A hidden host (its page/tab inactive →
  // display:none → offsetParent null) must be a no-op; only the visible host owns the mount.
  // setPage() re-renders on switch, so the console appears immediately when its page is shown.
  if (host.offsetParent === null) { host.__consoleWasHidden = true; return; }
  const id = sessionId(session);
  const status = String(session?.status || '').toLowerCase();
  const canStop = !['stopped', 'failed', 'lost', 'ended', 'completed', 'cancelled'].includes(status);
  const agent = agentForSession(session);
  const runtimeConfig = agent?.runtimeConfig || {};
  const runtime = String(agent?.runtime || '').toLowerCase();
  const hermesGatewayHttp = runtime === 'hermes'
    ? hermesGatewayUrlToHttp(runtimeConfig.gatewayUrl)
    : '';
  const codexAppServerUrl = runtime === 'codex' ? String(runtimeConfig.appServerUrl || '').trim() : '';
  const codexThreadId = runtime === 'codex'
    ? String(agent?.sessionHandle || runtimeConfig.threadId || agent?.runtimeState?.threadId || '').trim()
    : '';
  const codexIsLoopback = codexAppServerUrl && (() => {
    try { return ['127.0.0.1', 'localhost', '::1'].includes(new URL(codexAppServerUrl).hostname); }
    catch { return false; }
  })();
  const codexAttachable = codexAppServerUrl && codexIsLoopback;
  const agentIdForCodex = sessionAgentId(session) || '';
  const normalizedSessionMode = String(agent?.sessionMode || session?.sessionMode || session?.session_mode || '').toLowerCase();
  // THE SERVICE ALREADY ANSWERED THIS, and until now nobody asked. `records.py` emits
  // `consoleAvailable` on every agent row with the comment "the dashboard should hide the
  // button for these" -- and the dashboard derived it again instead, so the field was computed
  // on every request and read by nothing in the repo.
  //
  // THE TWO FAILED IN OPPOSITE DIRECTIONS. The service normalises an unknown or empty mode to
  // `resident` (`_normalize_session_mode`), so it hides the console; this line compared for
  // equality, so an empty mode meant NOT resident and offered a console that cannot attach.
  // Unreachable on the live fleet today -- all 47 agents carry resident or managed -- but a guard
  // that opens when its input is missing is decoration, so the fallback now folds exactly the way
  // `_normalize_session_mode` does: strip, lower, and anything outside SESSION_MODES is resident.
  // The contract declares that set as {managed, resident}, so the rule reduces to the one below;
  // a sibling test fails if a third mode is ever added to the contract.
  const isResident = typeof agent?.consoleAvailable === 'boolean'
    ? !agent.consoleAvailable
    : normalizedSessionMode.trim() !== 'managed';
  // When this console is embedded in the Chat conversation pane, the lifecycle actions
  // (Restart/Reset/Stop/Switch/Message-in-Chat) already live in the chat header + Details
  // drawer — so we don't duplicate them here (audit finding C5). Sessions page keeps the full set.
  const isChatSource = opts.source === 'chat';
  const connectActions = `${hermesGatewayHttp ? `<button class="ghost" data-action="open-hermes-tab" data-url="${esc(hermesGatewayHttp)}" title="Open the upstream Hermes browser UI in a separate tab">Open Hermes UI</button>` : ''}`
    + `${codexAttachable ? `<button class="ghost" data-action="codex-console-connect" data-agent-id="${esc(agentIdForCodex)}" data-app-server-url="${esc(codexAppServerUrl)}" data-thread-id="${esc(codexThreadId)}">Connect live console</button>` : ''}`;
  const _drawerAgentId = agent?.id || agentIdForCodex || '';
  const lifecycleActions = `${_drawerAgentId ? `<button class="ghost" data-agent-details="${esc(_drawerAgentId)}" title="Open the full lifecycle drawer (edit, handle, env, history, remove…)">Details</button>` : ''}`
    + `${agentIdForCodex ? `<button class="ghost" data-open-chat="${esc(agentIdForCodex)}" title="Message this agent in Chat">Message in Chat</button>` : ''}`
    + `${renderModeSwitchChip(agent)}`
    + `<button class="ghost" data-session-control="restart" data-session-id="${esc(id)}">Restart</button>`
    + `<button class="ghost" data-session-control="recreate" data-session-id="${esc(id)}" title="Restart with a FRESH context (discards native session)">Reset</button>`
    + `${canStop ? `<button class="ghost danger" data-session-control="stop" data-session-id="${esc(id)}">Stop</button>` : ''}`;
  const headerActions = isChatSource ? connectActions : (lifecycleActions + connectActions);
  // Lean action/meta bar — the agent name, status chip, and workspace already render in the panel
  // header (session-title / session-status / session-subtitle), so repeating them here was the
  // duplicated "doubled header". Keep only the runtime/env/mode meta line + the lifecycle actions.
  const headerCard = `
    <div class="session-actions-bar" data-kind="session" data-id="${esc(id)}">
      <small class="session-meta-line">${esc(sessionRuntime(session))} · ${esc(sessionEnvironmentId(session))}${hermesGatewayHttp ? ' · live tui_gateway' : ''}${codexAttachable ? ' · live app-server' : ''}${renderSessionModeLabel(agent)}</small>
      ${headerActions ? `<div class="contract-actions">${headerActions}</div>` : ''}
    </div>`;

  // For hermes resident agents with a live tui_gateway, embed the upstream
  // hermes web dashboard chat surface as an iframe. The dashboard runs at
  // http://127.0.0.1:<port>/ on the operator's machine; the operator's
  // browser is also on that machine, so loopback access works. This is
  // the real Ink Chat UI — interactive, typing-supported, full fidelity —
  // the same WS session the bridge attaches to via /api/ws. (See
  // ui-tui/src/gatewayClient.ts:resolveGatewayAttachUrl + the hermes
  // dashboard's embedded chat tab gated on HERMES_DASHBOARD_TUI=1.)
  // Widget choice is delegated to chooseSessionConsoleWidget (pure helper,
  // unit-tested in app.test.mjs). It caches the most-recent terminalId per
  // session so the widget doesn't oscillate when the server temporarily
  // clears runtime_state.virtualTerminalId — fixing the operator-reported
  // 2026-05-24 Bug #3 (iframe ↔ xterm flip mid-conversation triggered by
  // _stop_virtual_terminals_for_superseded_bridges running on every
  // list-sessions refresh).
  const widgetChoice = chooseSessionConsoleWidget({
    agent,
    sessionId: id,
    sessionMode: agent?.sessionMode || session?.sessionMode || session?.session_mode,
    sessionStatus: status,
    terminalStatus: session?.terminalStatus || session?.terminal_status || session?.terminal?.status,
    runtime,
    runtimeConfig,
    cache: state.sessionTerminals,
    hermesGatewayHttp,
    codexAppServerUrl,
    codexThreadId,
    codexAttachable,
    // Auto-attach: the session row itself may carry the live terminal id (terminal binding
    // recorded server-side on register/dispatch), so a running console mounts without the
    // operator pressing Start. chooseSessionConsoleWidget treats this as the lowest-priority
    // source (after runtime_state) so it never overrides the true owner's PTY.
    sessionTerminalId: session?.terminalId || session?.terminal?.id || session?.terminal_id || '',
  });
  const terminalId = widgetChoice.terminalId;
  const hasTerminal = widgetChoice.kind === 'xterm';
  // Console input is gated on whether a PTY xterm is actually MOUNTED (the chooser only mounts one
  // for a terminal that can represent the current owner) — NOT on session.status. The old gate
  // (canStop, from the narrow LIVE_SESSION_STATUSES) wrongly rejected input for a live-but-idle
  // `available` agent whose PTY exists, with a misleading "console is not live" toast — while the
  // backend /terminals/{id}/input accepts the keystroke anyway. (2026-06-29 fix.)
  const canConsoleInput = hasTerminal;
  const isVirtualTerminal = Boolean(agent?.runtimeState?.virtualTerminal);
  const ptyContainerId = hasTerminal ? `xterm-${terminalId}` : '';

  const ptyEmbed = hasTerminal
    ? `<div class="console-embed" data-kind="pty-xterm">
         <div class="console-embed-label">
           <span>${isVirtualTerminal ? 'Synth terminal' : 'Live PTY'} — <code>${esc(agent?.runtime || 'runtime')}</code> · terminal <code>${esc(terminalId)}</code>${isVirtualTerminal ? '' : ' · keystrokes flow back to the wrapper'}</span>
           <span class="console-toolbar">
             <span class="console-await-pill" id="console-await-pill" hidden>⌛ awaiting input</span>
             <button class="ghost" data-console-action="copy" title="Copy selection (or whole buffer) — Ctrl+Shift+C">Copy</button>
             <button class="ghost" data-console-action="refresh" title="Re-fetch the authoritative buffer and repaint">Refresh</button>
             ${canStop ? `<button class="ghost danger" data-console-action="stop" data-terminal-id="${esc(terminalId)}" title="Stop this terminal and return the agent to messenger ownership">Stop console</button>` : ''}
           </span>
         </div>
         <div id="${esc(ptyContainerId)}" class="xterm-host"></div>
       </div>`
    : '';

  // No live terminal yet (widgetChoice 'none') and the session is a MANAGED PTY-capable one:
  // offer to start a console. Resident agents are excluded — starting a managed console for a
  // resident identity would spawn a second process alongside the operator's own terminal
  // (audit finding C3); for those we show a switch-to-managed note instead. "Start fresh" is
  // only meaningful for pi without a saved handle (audit findings C1/C2), so we show a single
  // button otherwise — the truly-fresh path is the Reset (recreate) lifecycle action.
  // `!hermesGatewayHttp` was in this gate, so a hermes agent could NEVER be offered a console —
  // it got an iframe of the hermes web page instead. Hermes gets a real PTY console like any
  // other runtime (cms-tech-lead came up with 19KB of PTY output), so it may be started here too.
  const canStartConsole = widgetChoice.kind === 'none' && canStop && runtime && !isResident && !codexAttachable;
  // Runtime-agnostic: with no saved native handle there's nothing to resume, so starting IS a
  // fresh start (and sending freshContext lets handle-required runtimes start without a 409).
  // With a handle, a plain start resumes it; the truly-discard-and-restart path is Reset.
  const noSavedHandle = !String(agent?.sessionHandle || runtimeConfig.handle || runtimeConfig.threadId || '').trim();
  // A DEAD managed session previously showed NOTHING here (canStop false → no start offer,
  // and before the chooser's sessionDead guard it showed a stale dead-terminal xterm instead)
  // — the operator had no way to start the agent from the console view. Offer the session
  // RESTART (teardown + fresh spawn) as an explicit "Start agent" (operator ask 2026-07-02).
  const canStartDeadSession = widgetChoice.kind === 'none' && !canStop && runtime && !isResident;
  const startConsoleEmbed = canStartConsole
    ? `<div class="console-embed" data-kind="console-start">
         <div class="console-embed-label"><span>No live console for this session.</span></div>
         <div class="console-start-actions">
           ${noSavedHandle
             ? `<button class="primary" data-console-action="start-fresh" data-session-id="${esc(id)}" title="No saved native session — start a fresh console">Start fresh console</button>`
             : `<button class="primary" data-console-action="start" data-session-id="${esc(id)}" title="Resume this session's console">Start console</button>`}
         </div>
       </div>`
    : canStartDeadSession
    ? `<div class="console-embed" data-kind="console-start">
         <div class="console-embed-label"><span>This session is ${esc(status || 'stopped')} — no live console. The agent stays <em>available</em>: a message wakes it, or start it now.<span class="console-dead-cause" data-dead-cause-agent="${esc(agentIdForCodex || '')}"></span></span></div>
         <div class="console-start-actions">
           <button class="primary" data-session-control="restart" data-session-id="${esc(id)}" title="Spawn a fresh worker for this agent (resumes its saved session when one exists)">Start agent</button>
         </div>
       </div>`
    : '';

  // Resident agent with no embeddable widget: do NOT offer to start a managed console (that
  // would conflict with the operator's own CLI). Point them at the mode switch instead.
  const residentConsoleNote = (widgetChoice.kind === 'none' && isResident)
    ? `<div class="console-embed" data-kind="console-resident">
         <div class="console-embed-label"><span>${esc(agentIdForCodex || 'This agent')} is <strong>resident</strong> — its terminal is the CLI you launched (${esc(agent?.runtime || 'runtime')}-aify), not a dashboard-owned console.</span></div>
         <div class="console-start-actions">${renderModeSwitchChip(agent) || '<span class="em">Switch it to managed to get a dashboard console.</span>'}</div>
       </div>`
    : '';

  // The hermes gateway is NEVER embedded in the Console tab (operator, 2026-07-14: "it should
  // never show hermes local webpage.. cmon. we have button for that"). It hijacked the tab — you
  // opened Console to read the agent's console and got a web page — and because the iframe counted
  // as a live widget it suppressed the Start-console button too, so there was no way to reach the
  // console you came for. The gateway keeps its explicit "Open in new tab" action in the session
  // header (see connectActions). The chooser can no longer return `hermes-iframe`.
  const hermesIframe = '';

  // Codex doesn't have an upstream web UI to iframe, so we render the
  // JSON-RPC event stream ourselves. Operator clicks "Connect live
  // console" → browser WS direct to codex app-server (loopback only,
  // same security argument as the hermes iframe) → subscribes to the
  // agent's threadId → renders deltas + lifecycle markers + accepts
  // turn/start frames from the local input box. Falls back behind the
  // PTY render if the bridge owns a real terminal for this agent.
  const codexConsole = (widgetChoice.kind === 'codex-synth')
    ? `<div class="console-embed" data-kind="codex-app-server" data-codex-console="${esc(agentIdForCodex)}">
         <div class="console-embed-label">
           Codex live thread — attaches direct WS to <code>${esc(codexAppServerUrl)}</code>${codexThreadId ? ` · thread <code>${esc(codexThreadId)}</code>` : ''} (resident; switch to dashboard-spawned managed for true PTY render)
         </div>
         <div class="codex-console-stream" aria-live="polite"></div>
         <form class="codex-console-input" data-action="codex-console-send" data-agent-id="${esc(agentIdForCodex)}">
           <input type="text" placeholder="${codexThreadId ? 'Type to send turn/start into this thread...' : 'No threadId — read-only.'}" ${codexThreadId ? '' : 'disabled'}>
           <button type="submit" class="primary" ${codexThreadId ? '' : 'disabled'}>Send</button>
           <button type="button" class="ghost" data-action="codex-console-disconnect" data-agent-id="${esc(agentIdForCodex)}">Disconnect</button>
         </form>
       </div>`
    : '';

  // Re-render guard (2026-06-19): renderSessionConsole runs on EVERY poll-driven render.
  // Rewriting host.innerHTML destroys the live xterm DOM node, so the mounted PTY was
  // re-created every poll → "visible for a sec, then black" (operator-reported, hermes AND
  // claude). Skip the rewrite when nothing that changes the rendered widget changed AND the
  // xterm is still mounted to this host — the live terminal then persists across polls.
  // Live status/meta that must stay fresh lives in the panel header (renderSessionWorkspace),
  // not in this console host, so this guard does not stale anything visible.
  const consoleKey = JSON.stringify([id, widgetChoice.kind, terminalId, hermesGatewayHttp, codexAppServerUrl, codexThreadId, canStop, isChatSource, isVirtualTerminal]);
  const xtermStillMounted = hasTerminal && state.activeXterm
    && state.activeXterm.terminalId === terminalId
    && host.contains(state.activeXterm.container);
  if (host.dataset.consoleKey === consoleKey && (!hasTerminal || xtermStillMounted)) {
    if (hasTerminal && state.activeXterm) state.activeXterm.canInput = canConsoleInput;
    // Re-show resync (bughunt 2026-07-03): while this host was hidden (page switched
    // away) terminal_output frames hit the offsetParent early-return before lastSeq
    // advanced, so they were dropped — and an idle agent emits no new frame to trip the
    // seq-gap resync. On return, repaint the authoritative buffer once (mirrors the
    // WS-reconnect resync). Guard on the mounted xterm; clear the flag so it's one-shot.
    if (host.__consoleWasHidden) {
      host.__consoleWasHidden = false;
      if (hasTerminal && state.activeXterm && state.activeXterm.term) resyncActiveConsole().catch(() => {});
    }
    return;
  }
  host.dataset.consoleKey = consoleKey;
  host.__consoleWasHidden = false;

  // Close any live codex console WS before we rewrite innerHTML (bughunt 2026-07-03):
  // the rewrite detaches the codex container from the DOM but left the WebSocket open
  // with a stale map entry — one leaked socket per re-render / widget-kind change.
  if (agentIdForCodex) { try { codexConsoleClose(agentIdForCodex); } catch {} }

  host.innerHTML = `${headerCard}${ptyEmbed}${startConsoleEmbed}${residentConsoleNote}${hermesIframe}${codexConsole}`;

  // WHY IT IS DEAD, asked of the endpoint that already knows. The card above is built from the SESSION
  // row, which carries a terminal STATUS and no exit columns -- so it can say "stopped" and nothing
  // about a worker something killed. `GET /agents/{id}/console` has the code, the signal and the last
  // failure line; this fills them in after the paint. Best effort and only on the dead branch: the
  // placeholder does not exist otherwise, and a failed fetch leaves the card exactly as rendered.
  const deadCauseEl = host.querySelector('.console-dead-cause[data-dead-cause-agent]');
  if (deadCauseEl) {
    fillDeadConsoleCause(deadCauseEl, deadCauseEl.dataset.deadCauseAgent, { api }).catch(() => {});
  }

  // Mount xterm.js into the terminal container we just rendered. If a
  // different terminal was previously mounted, dispose its xterm first.
  // Query within `host` (not by global id) so a Chat-embedded console and the
  // Sessions console can't fight over a duplicate element id.
  if (hasTerminal) {
    const container = host.querySelector('.xterm-host');
    if (container) mountXtermForTerminal(terminalId, agentIdForCodex, container, { canInput: canConsoleInput }).catch(() => {});
  } else {
    disposeActiveXterm();
  }
}
