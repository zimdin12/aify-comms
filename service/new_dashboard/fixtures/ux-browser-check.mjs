// v0.7.7 dashboard UX in a real browser: the closed drawer leaves the Tab order, and the Start
// dialog's keys, refusal line and Create path work.
//
// Isolated Chromium, a local static server, the real styles.css, the drawer markup from index.html
// and the real dashboard modules. The control route is a stub here that refuses; no live service or
// dashboard is used. The plumbing is dashboard-browser-check.mjs's.
// Run: DASHBOARD_TEST_CHROME=<chrome executable> node service/new_dashboard/fixtures/ux-browser-check.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const chrome = process.env.DASHBOARD_TEST_CHROME;
assert.ok(chrome, 'Set DASHBOARD_TEST_CHROME to an existing Chromium executable; this fixture installs nothing');
const root = fileURLToPath(new URL('../', import.meta.url));
const profile = await mkdtemp(join(tmpdir(), 'ux-fixture-'));
const index = await readFile(join(root, 'index.html'), 'utf8');
const drawer = /<aside id="inspector"[\s\S]*?<\/aside>/.exec(index)?.[0];
assert.ok(drawer, 'the drawer markup was not found in index.html');
const REFUSAL = 'no environment bridge is available to run it';
const html = `<!doctype html><link rel="stylesheet" href="/styles.css"><button id="outside">outside</button>${drawer}
<input id="env-spawn-agent-id">
<script type="module">
import { state } from '/state.mjs';
import { setApiBase } from '/api-client.mjs';
import { initStartDialog, openStartDialog } from '/start-dialog.mjs';
setApiBase('/fixture-api', location.origin);
state.agents = [
  { id: 'sc-manager', status: 'stopped', sessionMode: 'managed' },
  { id: 'sc-coder', status: 'working', sessionMode: 'managed' },
  { id: 'pc-manager', status: 'available', sessionMode: 'resident' },
];
window.fixture = { pages: [], opened: [], openStartDialog };
initStartDialog({ setPage: (p) => fixture.pages.push(p), chatController: { open: (k) => fixture.opened.push(k) }, refreshSoon() {} });
window.ready = true;
</script>`;
const posts = [];

const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://fixture.invalid').pathname;
  if (path === '/') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
  if (path.startsWith('/fixture-api/')) {
    let body = ''; for await (const chunk of req) body += chunk;
    posts.push({ path, body });
    res.writeHead(409, { 'Content-Type': 'application/json' }).end(JSON.stringify({ detail: REFUSAL }));
    return;
  }
  const relative = decodeURIComponent(path).slice(1);
  if (relative.includes('..') || !/^[\w./-]+$/.test(relative)) { res.writeHead(403).end(); return; }
  try {
    const body = await readFile(join(root, relative));
    res.setHeader('Content-Type', extname(relative) === '.css' ? 'text/css' : 'text/javascript');
    res.end(body);
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;
const child = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--remote-debugging-pipe', `--user-data-dir=${profile}`], { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });
let stderr = ''; child.stderr.on('data', (b) => { stderr += b; });
let seq = 0, buffer = '', session;
const pending = new Map();
child.stdio[4].on('data', (b) => {
  buffer += b;
  let end;
  while ((end = buffer.indexOf('\0')) >= 0) {
    const msg = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
    const waiter = pending.get(msg.id);
    if (waiter) { pending.delete(msg.id); msg.error ? waiter.reject(new Error(JSON.stringify(msg.error))) : waiter.resolve(msg.result); }
  }
});
function cdp(method, params = {}, sessionId = session) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}\n${stderr}`)); }, 15000);
    pending.set(id, { resolve: (v) => { clearTimeout(timer); resolve(v); }, reject: (e) => { clearTimeout(timer); reject(e); } });
    child.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0');
  });
}
async function evaluate(expression) {
  const r = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
const settle = (ms) => evaluate(`new Promise((r) => setTimeout(r, ${ms}))`);
const KEYS = { Enter: 13, Escape: 27, ArrowDown: 40 };
async function press(key) {
  for (const type of ['rawKeyDown', 'keyUp']) {
    await cdp('Input.dispatchKeyEvent', { type, key, code: key, windowsVirtualKeyCode: KEYS[key] });
  }
}
async function type(text) {
  await evaluate("(() => { const i = document.querySelector('.start-dialog .dialog-input'); i.value = ''; i.dispatchEvent(new Event('input')); })()");
  await cdp('Input.insertText', { text });
}
const results = [];
async function check(name, run) {
  try { await run(); results.push({ name, pass: true }); } catch (error) { results.push({ name, pass: false, error: String(error?.message || error) }); }
}
const visibility = "getComputedStyle(document.getElementById('inspector')).visibility";
// Whether the drawer's Close button can take focus: the thing Tab would land on.
const focusable = "(() => { const b = document.getElementById('close-inspector'); document.getElementById('outside').focus(); b.focus(); return document.activeElement === b; })()";
const dialogText = "document.querySelector('.start-dialog')?.textContent ?? ''";

try {
  const { targetId } = await cdp('Target.createTarget', { url: 'about:blank' }, null);
  ({ sessionId: session } = await cdp('Target.attachToTarget', { targetId, flatten: true }, null));
  await cdp('Page.enable');
  await cdp('Page.navigate', { url: origin });
  for (let i = 0; i < 100 && !(await evaluate("window.ready === true && document.styleSheets.length > 0")); i += 1) await settle(50);

  await check('drawer closed: hidden and out of the Tab order', async () => {
    assert.equal(await evaluate(visibility), 'hidden');
    assert.equal(await evaluate(focusable), false, 'a closed drawer button took focus');
  });
  await check('drawer open: visible at once, and focusable', async () => {
    await evaluate("document.getElementById('inspector').classList.add('open')");
    assert.equal(await evaluate(visibility), 'visible', 'the drawer is hidden while it slides in');
    assert.equal(await evaluate(focusable), true, 'CONTROL: an open drawer button cannot take focus');
  });
  await check('drawer closing: still visible while it slides out, hidden after', async () => {
    await settle(300);
    await evaluate("document.getElementById('inspector').classList.remove('open')");
    assert.equal(await evaluate(visibility), 'visible', 'hidden before the slide, so the drawer vanished instead of sliding');
    await settle(400);
    assert.equal(await evaluate(visibility), 'hidden', 'still visible after the slide ended');
    assert.equal(await evaluate(focusable), false);
  });

  await check('start dialog: opens with the search focused and every agent listed', async () => {
    await evaluate('document.getElementById("outside").focus(); fixture.openStartDialog()');
    await settle(100);
    assert.equal(await evaluate("document.activeElement?.classList.contains('dialog-input')"), true, 'the search is not focused');
    assert.equal(await evaluate("document.querySelectorAll('.start-dialog [data-start-row]').length"), 3);
  });
  await check('start dialog: typing filters, and a refused start shows the reason and stays open', async () => {
    await type('sc-man');
    assert.equal(await evaluate("document.querySelectorAll('.start-dialog [data-start-row]').length"), 1);
    await press('Enter');
    await settle(200);
    assert.equal(posts.length, 1, 'Enter sent no start');
    assert.match(posts[0].path, /\/agents\/sc-manager\/control$/);
    assert.match(await evaluate(dialogText), new RegExp(REFUSAL), 'the refusal is not in the dialog');
  });
  await check('start dialog: arrows move, and a live agent opens its console', async () => {
    await type('sc-');
    await press('ArrowDown');
    assert.match(await evaluate("document.querySelector('.start-dialog .chat-rail-item.active')?.textContent ?? ''"), /sc-manager/);
    await type('sc-coder');
    await press('Enter');
    await settle(50);
    assert.deepEqual(await evaluate('fixture.opened'), ['dm:sc-coder']);
    assert.equal(await evaluate("!!document.querySelector('.start-dialog')"), false, 'the dialog stayed open');
  });
  await check('start dialog: a new name offers Create, which fills the spawn form', async () => {
    await evaluate('fixture.openStartDialog()');
    await settle(100);
    await type('fresh-agent');
    assert.match(await evaluate(dialogText), /Create "fresh-agent"/);
    await press('Enter');
    await settle(50);
    assert.equal(await evaluate("document.getElementById('env-spawn-agent-id').value"), 'fresh-agent');
    assert.ok((await evaluate('fixture.pages')).includes('environments'));
  });
  await check('start dialog: Escape closes it and gives focus back', async () => {
    await evaluate('document.getElementById("outside").focus(); fixture.openStartDialog()');
    await settle(100);
    await press('Escape');
    assert.equal(await evaluate("!!document.querySelector('.start-dialog')"), false);
    assert.equal(await evaluate("document.activeElement?.id"), 'outside');
  });
  console.log(JSON.stringify({ results, passed: results.filter((r) => r.pass).length, failed: results.filter((r) => !r.pass).length }, null, 2));
  if (results.some((r) => !r.pass)) process.exitCode = 1;
} finally {
  try { await cdp('Browser.close', {}, null); } catch {}
  if (child.exitCode === null) await new Promise((r) => child.once('exit', r));
  await new Promise((r) => server.close(r));
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
