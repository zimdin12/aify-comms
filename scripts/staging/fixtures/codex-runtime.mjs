// A no-provider runtime double. The real wrapper, installed Codex MCP configuration,
// stdio bridge, service and environment remain in the path. This is not native Codex qualification.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { brain } from './turn-brain.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const record = value => fs.appendFileSync(path.join(directory, 'runtime.jsonl'), JSON.stringify({ pid: process.pid,
  agentId: process.env.AIFY_AGENT_ID ?? null, ...value }) + '\n');

if (args.includes('--version') || args[0] === 'mcp') {
  const answer = spawnSync('/usr/local/bin/codex', args, { env: process.env, stdio: 'inherit' });
  process.exit(Number.isInteger(answer.status) ? answer.status : 1);
}
if (!args.includes('app-server')) {
  record({ role: 'tui', args });
  setInterval(() => {}, 1000);
} else {
  if (!args.includes('--listen')) throw new Error('staging runtime accepts only the wrapper WS app-server contract');
  const thread = process.env.CODEX_THREAD_ID || process.env.AIFY_SESSION_HANDLE || randomUUID();
  process.env.CODEX_THREAD_ID = thread;
  process.env.FAKE_CODEX_RESIDENT_THREAD = thread;
  process.env.FAKE_CODEX_SCRIPT = 'staging-reply';
  const home = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
  const configFile = path.join(home, 'config.toml');
  const parsed = spawnSync('python', ['-c',
    "import json,sys,tomllib;print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))['mcp_servers']['aify-comms']))", configFile],
  { encoding: 'utf8', timeout: 5000 });
  if (parsed.status !== 0) throw new Error('staging runtime could not read the installed Codex MCP entry');
  const entry = JSON.parse(parsed.stdout);
  if (typeof entry.command !== 'string' || !Array.isArray(entry.args) || !Array.isArray(entry.env_vars)) {
    throw new Error('installed Codex MCP command, argv or forwarded-name list is unavailable');
  }
  const environment = { ...entry.env, ...Object.fromEntries(entry.env_vars
    .filter(name => typeof name === 'string' && Object.hasOwn(process.env, name)).map(name => [name, process.env[name]])) };
  const bridge = path.join(os.homedir(), '.aify-comms', 'mcp', 'stdio');
  const originalPath = path.join(bridge, 'tests', 'fixtures', 'fake-codex-app-server.mjs');
  const original = fs.readFileSync(originalPath, 'utf8');
  function replaceOne(text, from, to) {
    if (text.split(from).length !== 2) throw new Error('maintained Codex fixture extension site changed');
    return text.replace(from, to);
  }
  let source = replaceOne(original, 'import readline from "node:readline";',
    'import readline from "node:readline";\nimport { createHash } from "node:crypto";');
  source = replaceOne(source, '  if (SCRIPT === "hello") {', `  if (SCRIPT === "staging-reply") {
    const { brain } = await import(${JSON.stringify(pathToFileURL(path.join(directory, 'turn-brain.mjs')).href)});
    await brain.respond(_params);
    send({jsonrpc:"2.0",method:"turn/completed",params:{turn:{id:turn,status:"completed"}}});
    return;
  }
  if (SCRIPT === "hello") {`);
  source = replaceOne(source, 'const wss = new WebSocketServer({ port, host });', `const wss = new WebSocketServer({ port, host,
    verifyClient: ({req}) => {
      if (!cliArgs.includes('--ws-auth')) return true;
      const expected = cliArgs[cliArgs.indexOf('--ws-token-sha256')+1];
      const token = String(req.headers.authorization||'').replace(/^Bearer /,'');
      return Boolean(token && expected && createHash('sha256').update(token).digest('hex')===expected);
    }
  });`);
  const generated = path.join(directory, `app-server-${process.pid}.mjs`);
  fs.writeFileSync(generated, source);
  const digest = text => createHash('sha256').update(text).digest('hex');
  record({ role: 'app-server', thread, originalSha256: digest(original), generatedSha256: digest(source),
    generated, configSha256: digest(fs.readFileSync(configFile)), forwardedNames: entry.env_vars,
    wsAuthRequired: args.includes('--ws-auth') });
  const client = new Client({ name: 'e4-runtime-fixture', version: '1.0.0' }, { capabilities: {} });
  const transport = new StdioClientTransport({ command: entry.command, args: [...entry.args], env: environment,
    cwd: process.cwd(), stderr: 'inherit' });
  // Bind the maintained server first; registration's discovery uses this same real WS endpoint.
  await import(pathToFileURL(generated).href);
  await client.connect(transport);
  brain.attach(client, directory);
  record({ role: 'mcp-connected', bridgePid: transport.pid });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, async () => {
    try { await client.close(); } finally { process.exit(0); }
  });
}
