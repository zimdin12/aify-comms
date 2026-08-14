// The Codex console: one WebSocket per agent, and the append path that writes its output into the page.
//
// Extracted from app.js in v0.5.4 as a measured closure — five declarations needing NOTHING at all, not
// even a sibling module. It is the only group left in app.js with a completely empty import surface.
//
// `codexConsoleConnections` moves with it because nothing outside this closure reads it. That is the
// ownership test used throughout the series: count the DIRECT readers of a mutable module-scope name, and
// if they are all inside the group, the group owns it. A map of live sockets is exactly the state that must
// have one owner — two copies would mean a close that never reaches the socket it meant to shut.
//
// THE `beforeunload` HANDLER STAYS IN app.js, and that is deliberate rather than an oversight. It closes
// every socket in this map on navigation, so it reads like it belongs here; but registering it at module
// scope would run `window.addEventListener` on import, making this module — and everything importing it —
// unloadable outside a browser, which is precisely what the extraction harness's purity rule forbids.
// app.js is the orchestrator and owns page lifecycle; this module owns the sockets. It imports
// `codexConsoleConnections` to do the cleanup.
//
// That split was not planned: the extractor's span rule tested for a trailing semicolon, and
// `const codexConsoleConnections = new Map(); // agentId → …` does not end in one, so the span ran on and
// swallowed the handler. The module then failed to import in Node, which is how it was caught. The
// extractor now strips trailing comments before any structural test.
//
// Every declaration is byte-identical to the one that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Leading comments stayed behind in
// app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its comments
// could not round-trip through the proof.

export const codexConsoleConnections = new Map(); // agentId → { ws, threadId, container }
export function codexConsoleClose(agentId) {
  const entry = codexConsoleConnections.get(agentId);
  if (!entry) return;
  try { entry.ws?.close(); } catch {}
  codexConsoleConnections.delete(agentId);
}
export function codexConsoleAppendLine(container, line, cls = '') {
  if (!container) return;
  const div = document.createElement('div');
  div.className = `codex-line ${cls}`.trim();
  div.textContent = line;
  container.appendChild(div);
  // Cap scrollback (the xterm path caps at 5000; this DOM stream had no bound → grew forever).
  while (container.childElementCount > 2000) container.removeChild(container.firstChild);
  container.scrollTop = container.scrollHeight;
}
export function codexConsoleAppendText(container, text) {
  if (!container) return;
  const lastLine = container.querySelector('.codex-line.delta:last-child');
  if (lastLine) {
    lastLine.textContent += text;
  } else {
    const div = document.createElement('div');
    div.className = 'codex-line delta';
    div.textContent = text;
    container.appendChild(div);
  }
  container.scrollTop = container.scrollHeight;
}
export function codexConsoleConnect(agentId, appServerUrl, threadId) {
  const wsUrl = String(appServerUrl || '').trim();
  if (!/^wss?:\/\//i.test(wsUrl)) return;
  codexConsoleClose(agentId);

  const sel = String(agentId).replace(/[\\"]/g, '\\$&'); // safe inside a quoted attribute selector
  const container = document.querySelector(`[data-codex-console="${sel}"] .codex-console-stream`);
  if (!container) return;
  container.innerHTML = '';
  codexConsoleAppendLine(container, `[connecting to ${wsUrl}…]`, 'sys');

  let ws;
  try { ws = new WebSocket(wsUrl); } catch (err) {
    codexConsoleAppendLine(container, `[connect error: ${err?.message || err}]`, 'err');
    return;
  }
  let nextId = 1;
  let activeTurn = null;
  const entry = { ws, threadId, container };
  codexConsoleConnections.set(agentId, entry);

  ws.addEventListener('open', () => {
    codexConsoleAppendLine(container, '[connected]', 'sys');
    ws.send(JSON.stringify({
      jsonrpc: '2.0',
      id: nextId++,
      method: 'initialize',
      params: { clientInfo: { name: 'aify-dashboard', title: 'aify dashboard console', version: '1.0' } },
    }));
    ws.send(JSON.stringify({ jsonrpc: '2.0', method: 'initialized', params: {} }));
    if (threadId) {
      ws.send(JSON.stringify({
        jsonrpc: '2.0',
        id: nextId++,
        method: 'thread/resume',
        params: { threadId, personality: 'friendly' },
      }));
      codexConsoleAppendLine(container, `[subscribed to thread ${threadId}]`, 'sys');
    } else {
      codexConsoleAppendLine(container, '[no threadId — will only see broadcast events]', 'sys');
    }
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(String(ev.data)); } catch { return; }
    const method = String(msg.method || '');
    const params = msg.params || {};
    if (method === 'turn/started' && params.turn?.id) {
      activeTurn = params.turn.id;
      codexConsoleAppendLine(container, `▶ turn started (${params.turn.id})`, 'turn');
    } else if (method === 'turn/completed') {
      const usage = params.turn?.usage || params.usage || {};
      const usageStr = usage.input_tokens || usage.output_tokens
        ? ` (in=${usage.input_tokens || 0} out=${usage.output_tokens || 0})`
        : '';
      codexConsoleAppendLine(container, `■ turn ended [${params.turn?.status || 'completed'}]${usageStr}`, 'turn');
      activeTurn = null;
    } else if (method === 'item/agentMessage/delta') {
      codexConsoleAppendText(container, String(params.delta || ''));
    } else if (method === 'item/started' && params.item?.id) {
      codexConsoleAppendLine(container, `→ ${params.item?.type || 'item'}`, 'tool');
    } else if (method === 'item/completed' && params.item?.id) {
      codexConsoleAppendLine(container, `✓ ${params.item?.type || 'item'}`, 'tool ok');
    } else if (method === 'error' && params.error?.message) {
      codexConsoleAppendLine(container, `✗ ${params.error.message}`, 'err');
    }
  });
  ws.addEventListener('close', (ev) => {
    codexConsoleAppendLine(container, `[disconnected: code=${ev.code}]`, 'sys');
    codexConsoleConnections.delete(agentId);
  });
  ws.addEventListener('error', () => {
    codexConsoleAppendLine(container, '[websocket error]', 'err');
  });
}

// ---------------------------------------------------------------------------------------------------
// Sending a turn into the codex console, appended in a later v0.5.4 slice.
//
// It joins this module rather than getting one of its own: it writes through the same socket registry and
// echoes into the same output stream the append path above owns. Its whole dependency surface is two names
// already declared here.

export function codexConsoleSendTurn(agentId, text) {
  const entry = codexConsoleConnections.get(agentId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1 || !entry.threadId) return;
  const trimmed = String(text || '').trim();
  if (!trimmed) return;
  const id = Math.floor(Math.random() * 1e9);
  entry.ws.send(JSON.stringify({
    jsonrpc: '2.0',
    id,
    method: 'turn/start',
    params: {
      threadId: entry.threadId,
      input: [{ type: 'text', text: trimmed }],
    },
  }));
  codexConsoleAppendLine(entry.container, `> ${trimmed}`, 'user');
}
