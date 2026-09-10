// Executed by https-origin.test.mjs with a process-local trusted fixture certificate.
// A real TLS socket and WebSocket handshake/frame, not a Caddy or browser deployment test.
import assert from 'node:assert/strict';
import https from 'node:https';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { once } from 'node:events';
import { resolveApiOrigin } from '../api-origin.mjs';
import { api, setApiBase } from '../api-client.mjs';
import { connectRealtimeSocket, initRealtimeSocket } from '../realtime-socket.mjs';
import { createTerminalInputPoster } from '../terminal-input.mjs';
import { state } from '../state.mjs';

const [keyPath, certPath] = process.argv.slice(2);
const requests = [];
const sockets = new Set();
const server = https.createServer({ key: readFileSync(keyPath), cert: readFileSync(certPath) }, async (req, res) => {
  let body = '';
  for await (const chunk of req) body += chunk;
  requests.push({ url: req.url, method: req.method, host: req.headers.host, body });
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ fixture: 'TLS response' }));
});
server.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
server.on('upgrade', (req, socket) => {
  requests.push({ url: req.url, host: req.headers.host });
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  const data = Buffer.from(JSON.stringify({ event: 'https_fixture', data: { received: true } }));
  assert.ok(data.length < 126);
  socket.write(Buffer.concat([Buffer.from([0x81, data.length]), data]));
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const origin = `https://127.0.0.1:${server.address().port}`;
let socket;
const deadline = setTimeout(() => { console.error('HTTPS fixture timed out'); process.exit(1); }, 10000);
try {
  // Both controls must refuse despite the child's process-local trusted certificate.
  const refused = (options) => new Promise((resolve, reject) => {
    https.get(origin, options, (res) => { res.resume(); reject(new Error('TLS unexpectedly accepted')); }).on('error', resolve);
  });
  assert.equal((await refused({ ca: [] })).code, 'DEPTH_ZERO_SELF_SIGNED_CERT');
  assert.equal((await refused({ ca: readFileSync(certPath), servername: 'wrong.invalid' })).code, 'ERR_TLS_CERT_ALTNAME_INVALID');

  globalThis.location = new URL(`${origin}/?apiOrigin=http://obsolete.invalid:8800`);
  const store = new Map([['aify.next.apiOrigin', 'http://obsolete.invalid:8800']]);
  globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  globalThis.document = { documentElement: { dataset: { defaultApiPort: '8800' } } };
  const resolved = resolveApiOrigin();
  assert.equal(resolved, origin);
  assert.equal(store.has('aify.next.apiOrigin'), false);
  setApiBase(`${resolved}/api/v1`, resolved);
  assert.deepEqual(await api('/probe'), { fixture: 'TLS response' });
  const post = createTerminalInputPoster({ api, terminalId: 'fixture-terminal', onError: (e) => { throw e; } });
  await post('native paste payload\r');

  const NativeWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = class extends NativeWebSocket {
    constructor(url) {
      assert.equal(url, `${origin.replace('https:', 'wss:')}/ws`);
      super(url);
      socket = this;
    }
  };
  const frame = new Promise((resolve) => {
    initRealtimeSocket({ dashboardNotifier: { handle: (event, data) => resolve({ event, data }) },
      evaluateFlowGates() {}, refreshSoon() {}, resyncActiveConsole: async () => {}, scheduleRenderAll() {} });
  });
  connectRealtimeSocket();
  assert.deepEqual(await frame, { event: 'https_fixture', data: { received: true } });
  assert.equal(state.realtimeConnected, true);
  assert.deepEqual(requests.map((r) => r.url), ['/api/v1/probe', '/api/v1/terminals/fixture-terminal/input', '/ws']);
  assert.ok(requests.every((r) => r.host === new URL(origin).host));
  assert.equal(requests[1].method, 'POST');
  assert.deepEqual(JSON.parse(requests[1].body), { body: 'native paste payload\r', requestedBy: 'dashboard' });
  console.log('verified HTTPS API, terminal input, WSS frame; untrusted and wrong-host TLS refused');
} finally {
  clearTimeout(deadline);
  if (socket) { socket.onclose = null; socket.close(); }
  for (const s of sockets) s.destroy();
  await new Promise((resolve) => server.close(resolve));
}
