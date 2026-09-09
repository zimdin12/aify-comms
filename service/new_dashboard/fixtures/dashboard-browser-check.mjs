// Isolated Chromium fixture. No live service, PTY, user profile or installed dashboard is used.
// Run: DASHBOARD_TEST_CHROME=<chrome executable> node service/new_dashboard/fixtures/dashboard-browser-check.mjs
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const chrome = process.env.DASHBOARD_TEST_CHROME;
assert.ok(chrome, 'Set DASHBOARD_TEST_CHROME to an existing Chromium executable; this fixture installs nothing');
const root = fileURLToPath(new URL('../', import.meta.url));
const profile = await mkdtemp(join(tmpdir(), 'dashboard-fixture-'));
const requests = [];
const sourceRef = process.env.DASHBOARD_TEST_REF;
if (sourceRef) assert.match(sourceRef, /^[a-f0-9]{8,40}$/);
let terminal = { snapshot: '\x1b[H\x1b[2J', outputSeq: 0, renderedCols: 80 };
let holdSnapshot = false, releaseSnapshot;
const html = `<!doctype html><link rel="stylesheet" href="/vendor/xterm.css">
<div id="console" style="width:800px;height:400px"></div>
<script src="/vendor/xterm.js"></script><script type="module">
import { mountXtermForTerminal } from '/xterm-mount.mjs';
import { disposeActiveXterm } from '/xterm-lifecycle.mjs';
import { state } from '/state.mjs';
import { setApiBase } from '/api-client.mjs';
import { resyncActiveConsole } from '/console-actions.mjs';
import { connectRealtimeSocket, initRealtimeSocket } from '/realtime-socket.mjs';
setApiBase('/fixture-api', location.origin);
initRealtimeSocket({dashboardNotifier:{handle(){}}, evaluateFlowGates(){}, refreshSoon(){}, resyncActiveConsole, scheduleRenderAll(){}});
connectRealtimeSocket();
window.fixture = { state, disposeActiveXterm, resyncActiveConsole,
  mount: () => mountXtermForTerminal('fixture-terminal', 'fixture-agent', document.getElementById('console'), {}, {resyncActiveConsole}),
  flush: () => new Promise(r => state.activeXterm.term.write('', r)),
  text: () => { const b=state.activeXterm.term.buffer.active; return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\\n'); }
};
await fixture.mount(); window.ready = true;
</script>`;
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://fixture.invalid');
    if (url.pathname.startsWith('/fixture-api/')) {
      let body = ''; for await (const chunk of req) body += chunk;
      requests.push({ path: url.pathname, method: req.method, body: body ? JSON.parse(body) : null });
      res.setHeader('Content-Type', 'application/json');
      const answer = JSON.stringify({ terminal });
      if (req.method === 'GET' && holdSnapshot) {
        holdSnapshot = false;
        await new Promise(r => { releaseSnapshot = r; });
      }
      res.end(answer);
      return;
    }
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
    const relative = decodeURIComponent(url.pathname).slice(1);
    if (relative.includes('..') || !/^[\w./-]+$/.test(relative)) { res.writeHead(403).end(); return; }
    res.setHeader('Content-Type', extname(relative) === '.css' ? 'text/css' : 'text/javascript');
    res.end(sourceRef
      ? execFileSync('git', ['show', `${sourceRef}:service/new_dashboard/${relative}`], {cwd: root})
      : await readFile(join(root, relative)));
  } catch { res.writeHead(404).end(); }
});
let wire;
server.on('upgrade', (req, socket) => {
  if (req.url !== '/ws') { socket.destroy(); return; }
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  socket.on('error', () => {});
  wire = socket;
});
function frame(seq, output) {
  const body = Buffer.from(JSON.stringify({event:'terminal_output',data:{terminalId:'fixture-terminal',agentId:'fixture-agent',seq,output}}));
  // RFC 6455 requires the shortest length encoding. Chromium rejects an extended
  // length for a short frame, closing the socket before the application sees it.
  assert.ok(body.length <= 65535, 'fixture frame fits the supported encoding');
  const header = Buffer.alloc(body.length < 126 ? 2 : 4);
  header[0] = 0x81;
  header[1] = body.length < 126 ? body.length : 126;
  if (body.length >= 126) header.writeUInt16BE(body.length, 2);
  wire.write(Buffer.concat([header,body]));
}
await new Promise(r => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;
const child = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--remote-debugging-pipe', `--user-data-dir=${profile}`], {stdio:['ignore','ignore','pipe','pipe','pipe']});
let stderr = ''; child.stderr.on('data', b => { stderr += b; });
let seq = 0, buffer = '', session;
const pending = new Map();
child.stdio[4].on('data', b => {
  buffer += b;
  let end;
  while ((end = buffer.indexOf('\0')) >= 0) {
    const msg = JSON.parse(buffer.slice(0,end)); buffer=buffer.slice(end+1);
    const waiter=pending.get(msg.id);
    if (waiter) {pending.delete(msg.id); msg.error ? waiter.reject(new Error(JSON.stringify(msg.error))) : waiter.resolve(msg.result);}
  }
});
function cdp(method, params={}, sessionId=session) {
  return new Promise((resolve,reject)=>{
    const id=++seq;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method}\n${stderr}`));},15000);
    pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});
    child.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0');
  });
}
async function evaluate(expression) {
  const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});
  if(r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
async function waitFor(expression) {
  const end=Date.now()+5000;
  while(Date.now()<end) { if(await evaluate(expression)) return; await new Promise(r=>setTimeout(r,10)); }
  throw new Error(`Timed out: ${expression}`);
}
async function key(key, code, modifiers=0) {
  await cdp('Input.dispatchKeyEvent',{type:'rawKeyDown',key,code,windowsVirtualKeyCode:key==='Enter'?13:86,modifiers});
  await cdp('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:key==='Enter'?13:86,modifiers});
}
const results = [];
async function check(name, run) {
  try { const evidence = await run(); results.push({name,pass:true,...evidence}); }
  catch(e) { results.push({name,pass:false,error:e.message}); }
}
async function beginHeld(expression) {
  holdSnapshot=true; releaseSnapshot=null;
  await evaluate(`window.operation = ${expression}; void 0`);
  const end=Date.now()+5000;
  while(!releaseSnapshot && Date.now()<end) await new Promise(r=>setTimeout(r,10));
  assert.ok(releaseSnapshot,'snapshot request reached the fixture');
}
async function finishHeld() {
  releaseSnapshot(); releaseSnapshot=null;
  await evaluate('window.operation');
  await evaluate('fixture.flush()');
}
const inputs = () => requests.filter(r=>r.path.endsWith('/input')).map(r=>r.body.body);
try {
  const {targetId}=await cdp('Target.createTarget',{url:'about:blank'},null);
  ({sessionId:session}=await cdp('Target.attachToTarget',{targetId,flatten:true},null));
  await cdp('Page.enable');
  await cdp('Browser.grantPermissions',{origin,permissions:['clipboardReadWrite','clipboardSanitizedWrite']},null);
  await cdp('Page.navigate',{url:origin});
  await waitFor('window.ready === true && fixture.state.realtimeConnected');
  console.log(JSON.stringify({source:sourceRef || 'working tree', browser:await cdp('Browser.getVersion',{},null)}));
  // Native Chromium paste, real vendored xterm, real onData, serialized poster and HTTP request.
  for (const [name,modifiers,bracketed,clipboardApi] of [
    ['Ctrl+V',2,false,true], ['Ctrl+Shift+V',10,false,true],
    ['bracketed Ctrl+V',2,true,true], ['Ctrl+V without async clipboard API',2,false,false],
  ]) await check(name, async () => {
    requests.length=0;
    await evaluate(`navigator.clipboard.writeText('test')`);
    await evaluate(`fixture.state.activeXterm.term.write(${JSON.stringify('\x1b[?2004')} + ${JSON.stringify(bracketed?'h':'l')}); fixture.flush()`);
    if(!clipboardApi) await evaluate(`Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true})`);
    await key('v','KeyV',modifiers);
    await new Promise(r=>setTimeout(r,150));
    await key('Enter','Enter');
    await new Promise(r=>setTimeout(r,100));
    if(!clipboardApi) await evaluate('delete navigator.clipboard');
    const actual=inputs();
    console.log(JSON.stringify({name,inputs:actual}));
    assert.deepEqual(actual,[bracketed?'\x1b[200~test\x1b[201~':'test','\r']);
    return {inputs:actual};
  });
  await check('same-container reuse and dispose/remount keep one input listener', async () => {
    for(let i=0;i<3;i++) await evaluate('fixture.mount()');
    for(let i=0;i<3;i++) await evaluate('fixture.disposeActiveXterm(); fixture.mount()');
    requests.length=0;
    await evaluate(`navigator.clipboard.writeText('test')`);
    await key('v','KeyV',2); await new Promise(r=>setTimeout(r,100));
    await key('v','KeyV',2); await new Promise(r=>setTimeout(r,100));
    await key('Enter','Enter'); await new Promise(r=>setTimeout(r,100));
    assert.deepEqual(inputs(),['test','test','\r'],'two deliberate pastes are kept, each once');
    return {inputs:inputs()};
  });
  await check('native paste cannot write to a read-only console', async () => {
    requests.length=0;
    await evaluate('fixture.state.activeXterm.canInput=false');
    await key('v','KeyV',2); await new Promise(r=>setTimeout(r,150));
    await evaluate('fixture.state.activeXterm.canInput=true');
    assert.deepEqual(inputs(),[]);
  });
  await check('WebSocket retransmit is rendered once; equal text at a new sequence is retained', async () => {
    frame(1,'ASSISTANT_REPLY\r\n'); frame(1,'ASSISTANT_REPLY\r\n'); frame(2,'NEXT\r\n');
    await waitFor('fixture.state.activeXterm.lastSeq===2'); await evaluate('fixture.flush()');
    assert.equal((await evaluate('fixture.text()')).split('ASSISTANT_REPLY').length-1,1);
    frame(3,'ASSISTANT_REPLY\r\n');
    await waitFor('fixture.state.activeXterm.lastSeq===3'); await evaluate('fixture.flush()');
    assert.equal((await evaluate('fixture.text()')).split('ASSISTANT_REPLY').length-1,2);
  });
  await check('mount snapshot and matching held frame render assistant output once', async () => {
    terminal={snapshot:'\x1b[H\x1b[2JMOUNT_REPLY\r\n',outputSeq:4,renderedCols:80};
    await evaluate('fixture.disposeActiveXterm()');
    await beginHeld('fixture.mount()');
    frame(4,'MOUNT_REPLY\r\n'); frame(5,'MOUNT_NEXT\r\n');
    await new Promise(r=>setTimeout(r,80));
    await finishHeld();
    const text=await evaluate('fixture.text()');
    console.log(JSON.stringify({name:'mount overlap',text}));
    assert.equal(text.split('MOUNT_REPLY').length-1,1);
    assert.equal(text.split('MOUNT_NEXT').length-1,1);
  });
  await check('recovery preserves a quiet final frame received during its HTTP wait', async () => {
    terminal={snapshot:'\x1b[H\x1b[2JRECOVERY_REPLY\r\n',outputSeq:5,renderedCols:80};
    await evaluate('fixture.resyncActiveConsole()');
    await beginHeld('fixture.resyncActiveConsole()');
    frame(6,'FINAL_REPLY\r\n');
    await new Promise(r=>setTimeout(r,80));
    await finishHeld();
    const text=await evaluate('fixture.text()');
    console.log(JSON.stringify({name:'quiet final frame',text}));
    assert.equal(text.split('FINAL_REPLY').length-1,1);
    assert.equal(await evaluate('fixture.state.activeXterm.lastSeq'),6);
  });
  await check('gap recovery rejoins the stream instead of fetching on the next frame', async () => {
    terminal={snapshot:'\x1b[H\x1b[2JGAP_SNAPSHOT\r\n',outputSeq:8,renderedCols:80};
    requests.length=0;
    holdSnapshot=true; releaseSnapshot=null;
    frame(9,'AFTER_GAP\r\n');
    const end=Date.now()+5000;
    while(!releaseSnapshot && Date.now()<end) await new Promise(r=>setTimeout(r,10));
    assert.ok(releaseSnapshot);
    frame(10,'DURING_RECOVERY\r\n');
    await new Promise(r=>setTimeout(r,80));
    releaseSnapshot(); releaseSnapshot=null;
    await waitFor('!fixture.state.activeXterm.resyncing');
    frame(11,'LIVE_AGAIN\r\n');
    await new Promise(r=>setTimeout(r,100)); await evaluate('fixture.flush()');
    const gets=requests.filter(r=>r.method==='GET').length;
    const text=await evaluate('fixture.text()');
    console.log(JSON.stringify({name:'gap recovery',gets,text}));
    assert.equal(gets,1); assert.ok(text.includes('LIVE_AGAIN'));
  });
  console.log(JSON.stringify({results,passed:results.filter(r=>r.pass).length,failed:results.filter(r=>!r.pass).length},null,2));
  if(results.some(r=>!r.pass)) process.exitCode=1;
} finally {
  try { await cdp('Browser.close',{},null); } catch {}
  if(child.exitCode===null) await new Promise(r=>child.once('exit',r));
  wire?.destroy();
  await new Promise(r=>server.close(r));
  await rm(profile,{recursive:true,force:true,maxRetries:10,retryDelay:100});
}
