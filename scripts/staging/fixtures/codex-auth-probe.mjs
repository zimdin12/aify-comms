// E3, inside the staging host: one launch's Codex app-server authentication. Prints no token, only facts about it.
// The token is read from the app-server's own /proc environ: same user, the exposure aify-wrapper's contract names.
import fs from 'node:fs';
import http from 'node:http';
import { createHash, randomBytes } from 'node:crypto';

const [agentId] = process.argv.slice(2);
const read = (pid, file) => fs.readFileSync(`/proc/${pid}/${file}`, 'utf8').split('\0').filter(Boolean);
const processes = fs.readdirSync('/proc').filter(name => /^\d+$/.test(name)).flatMap(pid => {
  try { return [{ pid: Number(pid), argv: read(pid, 'cmdline'), env: read(pid, 'environ') }]; } catch { return []; }
});
const server = processes.find(row => row.argv.includes('app-server') && row.argv.includes('--listen'));
if (!server) { console.log(JSON.stringify({ server: null })); process.exit(0); }
const token = server.env.find(line => line.startsWith('AIFY_CODEX_APP_SERVER_TOKEN='))?.split('=')[1] ?? '';
const flag = name => server.argv[server.argv.indexOf(name) + 1];
const listen = flag('--listen');
function upgrade(secret) {
  const { hostname, port } = new URL(listen.replace('ws:', 'http:'));
  return new Promise(resolve => {
    const request = http.request({ host: hostname, port, path: '/', headers: { Connection: 'Upgrade', Upgrade: 'websocket',
      'Sec-WebSocket-Version': '13', 'Sec-WebSocket-Key': randomBytes(16).toString('base64'),
      ...(secret ? { Authorization: `Bearer ${secret}` } : {}) } });
    request.on('upgrade', (response, socket) => { socket.destroy(); resolve(response.statusCode); });
    request.on('response', response => { response.resume(); resolve(response.statusCode); });
    request.on('error', error => resolve(error.code));
    request.end();
  });
}
function initialize() {
  return new Promise(resolve => {
    const socket = new WebSocket(listen, { headers: { Authorization: `Bearer ${token}` } });
    socket.onopen = () => socket.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { clientInfo: { name: 'staging-e3', title: 'staging-e3', version: '0' } } }));
    socket.onmessage = event => { const reply = JSON.parse(event.data); if (reply.id === 1) { socket.close(); resolve(reply.result ? 'answered' : 'error'); } };
    socket.onerror = () => resolve('socket-error');
    setTimeout(() => resolve('timeout'), 10_000);
  });
}
const record = await (await fetch(`http://service:8800/api/v1/agents/${agentId}`, { headers: { 'x-api-key': process.env.AIFY_API_KEY } })).text();
const toml = fs.readFileSync(`${process.env.HOME}/.codex/config.toml`, 'utf8');
const section = toml.split(/^\[/m).find(part => part.startsWith('mcp_servers.aify-comms]')) ?? '';
console.log(JSON.stringify({
  server: { pid: server.pid, listen, wsAuth: flag('--ws-auth'), verifier: flag('--ws-token-sha256') },
  tokenShaped: /^[A-Za-z0-9_-]{43}$/.test(token),
  verifierIsTokenSha256: createHash('sha256').update(token).digest('hex') === flag('--ws-token-sha256'),
  argvHolders: token ? processes.filter(row => row.argv.some(arg => arg.includes(token))).map(row => row.pid) : null,
  recordHoldsToken: !token || record.includes(token),
  noToken: await upgrade(''), wrongToken: await upgrade('x'.repeat(43)), rightToken: await upgrade(token), initialize: await initialize(),
  envVars: (/^env_vars\s*=\s*\[([^\]]*)\]/m.exec(section)?.[1] ?? '').match(/"[^"]+"/g)?.map(name => name.slice(1, -1)) ?? [],
  tomlHoldsToken: !token || toml.includes(token),
}));
