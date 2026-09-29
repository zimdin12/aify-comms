// A closed drawer is out of the Tab order, and still slides (v0.7.7, dashboard item 5).
//
// Isolated Chromium, a local static server, the real styles.css and the drawer's markup from
// index.html. No live service or dashboard is used. The plumbing is dashboard-browser-check.mjs's.
// Run: DASHBOARD_TEST_CHROME=<chrome executable> node service/new_dashboard/fixtures/drawer-browser-check.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const chrome = process.env.DASHBOARD_TEST_CHROME;
assert.ok(chrome, 'Set DASHBOARD_TEST_CHROME to an existing Chromium executable; this fixture installs nothing');
const root = fileURLToPath(new URL('../', import.meta.url));
const profile = await mkdtemp(join(tmpdir(), 'drawer-fixture-'));
const index = await readFile(join(root, 'index.html'), 'utf8');
const drawer = /<aside id="inspector"[\s\S]*?<\/aside>/.exec(index)?.[0];
assert.ok(drawer, 'the drawer markup was not found in index.html');
const html = `<!doctype html><link rel="stylesheet" href="/styles.css"><button id="outside">outside</button>${drawer}`;

const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://fixture.invalid').pathname;
  if (path === '/') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
  if (path === '/styles.css') { res.setHeader('Content-Type', 'text/css'); res.end(await readFile(join(root, 'styles.css'))); return; }
  res.writeHead(404).end();
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
const results = [];
async function check(name, run) {
  try { await run(); results.push({ name, pass: true }); } catch (error) { results.push({ name, pass: false, error: String(error?.message || error) }); }
}
const visibility = "getComputedStyle(document.getElementById('inspector')).visibility";
// Whether the drawer's Close button can take focus: the thing Tab would land on.
const focusable = "(() => { const b = document.getElementById('close-inspector'); document.getElementById('outside').focus(); b.focus(); return document.activeElement === b; })()";
const settle = (ms) => evaluate(`new Promise((r) => setTimeout(r, ${ms}))`);

try {
  const { targetId } = await cdp('Target.createTarget', { url: 'about:blank' }, null);
  ({ sessionId: session } = await cdp('Target.attachToTarget', { targetId, flatten: true }, null));
  await cdp('Page.enable');
  await cdp('Page.navigate', { url: origin });
  for (let i = 0; i < 50 && !(await evaluate("document.readyState === 'complete' && document.styleSheets.length > 0")); i += 1) await settle(50);

  await check('closed: hidden and out of the Tab order', async () => {
    assert.equal(await evaluate(visibility), 'hidden');
    assert.equal(await evaluate(focusable), false, 'a closed drawer button took focus');
  });
  await check('open: visible at once, and focusable', async () => {
    await evaluate("document.getElementById('inspector').classList.add('open')");
    assert.equal(await evaluate(visibility), 'visible', 'the drawer is hidden while it slides in');
    assert.equal(await evaluate(focusable), true, 'CONTROL: an open drawer button cannot take focus');
  });
  await check('closing: still visible while it slides out, hidden after', async () => {
    await settle(300);
    await evaluate("document.getElementById('inspector').classList.remove('open')");
    assert.equal(await evaluate(visibility), 'visible', 'hidden before the slide, so the drawer vanished instead of sliding');
    await settle(400);
    assert.equal(await evaluate(visibility), 'hidden', 'still visible after the slide ended');
    assert.equal(await evaluate(focusable), false);
  });
  console.log(JSON.stringify({ results, passed: results.filter((r) => r.pass).length, failed: results.filter((r) => !r.pass).length }, null, 2));
  if (results.some((r) => !r.pass)) process.exitCode = 1;
} finally {
  try { await cdp('Browser.close', {}, null); } catch {}
  if (child.exitCode === null) await new Promise((r) => child.once('exit', r));
  await new Promise((r) => server.close(r));
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
