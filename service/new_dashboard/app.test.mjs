#!/usr/bin/env node
// Structural regression guards for the Dashboard Next files that are NOT pure modules
// (index.html / app.js orchestrator / styles.css). Pure-helper behavior is tested by direct
// import in console-chooser.test.mjs and status.test.mjs (DASHBOARD_REBUILD_PLAN §0.6).
//
// Run: node --test service/new_dashboard/app.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { createTerminalInputHandler, createTerminalInputPoster } from "./terminal-input.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (name) => fs.readFileSync(path.join(__dirname, name), "utf8");

test("app.js loads as an ES module (Phase 0.1) and imports the extracted pure cores", () => {
  const html = read("index.html");
  assert.match(html, /<script type="module" src="\/assets\/app\.js">/, "index.html must load app.js as a module");
  const source = read("app.js");
  assert.match(source, /import \{ esc, relTime, tsMs \} from '\.\/util\.js'/);
  assert.match(source, /import \{ createTerminalInputPoster, createTerminalInputHandler \} from '\.\/terminal-input\.mjs'/);
  assert.match(source, /from '\.\/status\.js'/);
  assert.match(source, /from '\.\/console-chooser\.js'/);
  // The extracted definitions must be GONE from app.js (no duplicate source of truth).
  assert.ok(!/\nconst STATUS_KINDS = \{/.test(source), "STATUS_KINDS must live only in status.js");
  assert.ok(!/\nfunction chooseSessionConsoleWidget\(/.test(source), "chooser must live only in console-chooser.js");
});

test("styles.css keeps ready internal: ready normalizes to online, no separate ready dot", () => {
  const styles = read("styles.css");
  assert.ok(!/\.status-dot\.ready\b/.test(styles), "no separate ready dot; ready is internal");
  // The bare .status-* text-color aliases were removed (dead: status.js only emits chip tone +
  // dot kind classes, never a bare .status-ready/.status-online text class).
});

test("chat composer queue defaults to live send (not queued)", () => {
  const html = read("index.html");
  // WS-J: the Sessions composer was removed (it duplicated Chat); messaging lives in the chat
  // composer. Queue-if-busy must not default to checked.
  const queueInput = html.match(/<input[^>]+id="chat-queue"[^>]*>/);
  assert.ok(queueInput, "chat queue checkbox must exist");
  assert.ok(!/\schecked(\s|>|=)/.test(queueInput[0]),
    "normal Send must not default queueIfBusy=true; Queue is an explicit operator choice");
});

test("click handling processes mode switch before session row selection", () => {
  const source = read("app.js");
  const modeSwitchIndex = source.indexOf("const modeSwitchButton = event.target.closest('[data-mode-switch]')");
  const sessionSelectIndex = source.indexOf("const sessionSelect = event.target.closest('[data-session-select]')");
  assert.ok(modeSwitchIndex >= 0 && sessionSelectIndex >= 0, "both click handlers must exist");
  assert.ok(modeSwitchIndex < sessionSelectIndex,
    "mode switch must be handled before session row selection so nested rail chips are clickable");
});

test("Work Loop board view: toggle, renderer, and card reuse are wired", () => {
  const html = read("index.html");
  const source = read("app.js");
  const styles = read("styles.css");
  // The List/Board toggle exists in the Work Loop toolbar.
  assert.match(html, /data-contract-view="list"/, "List toggle button must exist");
  assert.match(html, /data-contract-view="board"/, "Board toggle button must exist");
  // The renderer + column model exist and the board reuses contractCard (so selection/actions work).
  assert.match(source, /const CONTRACT_BOARD_COLUMNS = \[/, "board column model must exist");
  assert.match(source, /function renderContractBoard\(/, "board renderer must exist");
  assert.match(source, /overdue.*match:.*c\.overdue/, "overdue must be a board column that wins on the flag");
  assert.ok(source.includes("state.contractView === 'board'"), "renderContracts must branch on the persisted view");
  // The click handler is scoped to the button (must not swallow card actions — same lesson as work-view).
  assert.match(source, /event\.target\.closest\('button\[data-contract-view\]'\)/,
    "contract-view handler must be scoped to button[data-contract-view]");
  // The layout is persisted; list stays the default.
  assert.match(source, /localStorage\.setItem\('aifyContractView'/, "board view must persist");
  // CSS for the board columns is present.
  assert.match(styles, /\.contract-list\.is-board/, "is-board container style must exist");
  assert.match(styles, /\.contract-board-col/, "board column style must exist");
});

test("xterm remount guard checks container identity, not just terminal id", () => {
  const source = read("app.js");
  assert.match(source,
    /state\.activeXterm\.container === container[\s\S]*container\.isConnected !== false/,
    "mountXtermForTerminal must remount when render recreated the host container");
});

test("automatic console resync never self-excites a PTY resize loop", () => {
  const source = read("app.js");
  assert.match(source, /async function resyncActiveConsole\(\{ forceRepaint = false \} = \{\}\)/,
    "resync must distinguish passive recovery from an explicit operator repaint");
  assert.match(source, /if \(forceRepaint && entry\.ownsPty\)/,
    "only the explicit Refresh action may nudge the PTY width");
  assert.match(source, /resyncActiveConsole\(\{ forceRepaint: true \}\)/,
    "the visible Refresh action must retain the one-shot full repaint escape hatch");
  assert.match(source, /entry\.lastSeq = Math\.max\(/,
    "a snapshot fetched during live output must not roll the sequence watermark backwards");
});

test("managed PTY keeps raw terminal semantics and ordered input", () => {
  const source = read("app.js");
  const terminalInput = read("terminal-input.mjs");
  const html = read("index.html");
  assert.match(source, /convertEol:\s*false/,
    "real PTY output must not rewrite LF into CRLF");
  assert.match(terminalInput, /let pending = Promise\.resolve\(\)/,
    "terminal input posts must be serialized so keystrokes cannot arrive out of order");
  assert.match(html, /addon-unicode11\.js/,
    "the managed console should use the same Unicode width tables as Hermes dashboard");
  assert.match(html, /addon-web-links\.js/,
    "terminal links should be clickable like Hermes dashboard");
  assert.match(source, /Unicode11Addon/,
    "the Unicode 11 addon must be activated, not only loaded");
  assert.match(source, /WebLinksAddon/,
    "the web-links addon must be activated, not only loaded");
});

test("vendored PTY fidelity addons expose the browser globals app.js loads", () => {
  const context = {};
  context.self = context;
  vm.createContext(context);
  for (const asset of ["addon-unicode11.js", "addon-web-links.js"]) {
    vm.runInContext(read(`vendor/${asset}`), context, { filename: asset });
  }
  assert.equal(typeof context.Unicode11Addon?.Unicode11Addon, "function");
  assert.equal(typeof context.WebLinksAddon?.WebLinksAddon, "function");
});

test("ownsPty is positively-managed (fails closed), not !== 'resident' (fails open)", () => {
  const source = read("app.js");
  // The resize decision must OWN the PTY only when the mode is POSITIVELY 'managed'. The old
  // `!== 'resident'` failed open: an unknown / missing-agent / empty mode read as owned and the
  // dashboard would SIGWINCH the operator's own resident terminal.
  assert.match(source, /const ownsPty = _mode === 'managed'/,
    "ownsPty must derive from _mode === 'managed'");
  assert.ok(!/ownsPty = String\(agentForTerminal\(terminalId\)\?\.sessionMode \|\| ''\)\.toLowerCase\(\) !== 'resident'/.test(source),
    "the fail-open `!== 'resident'` derivation must be gone");
  // ...and it must fall back to the session row's own mode so a not-yet-populated state.agents
  // can't flip a resident console to owned.
  assert.match(source, /_sess\?\.sessionMode \|\| _sess\?\.session_mode/,
    "ownsPty must fall back to the session row's sessionMode/session_mode");
});

test("xterm setup imitates Hermes terminal-fidelity settings", () => {
  const source = read("app.js");
  // Legibility + Unicode11 correctness + selection ergonomics, studied from Hermes' dashboard.
  assert.match(source, /allowProposedApi: true/, "Unicode11 needs allowProposedApi");
  assert.match(source, /minimumContrastRatio: 4\.5/, "clamp low-contrast ANSI (Hermes 'VS Code secret sauce')");
  assert.match(source, /macOptionClickForcesSelection: true/);
  assert.match(source, /rightClickSelectsWord: true/);
  assert.match(source, /const postTerminalInput = createTerminalInputPoster\(/);
  assert.match(source, /term\.onData\(createTerminalInputHandler\(\{/);
});

test("terminal input forwards SGR mouse reports unchanged and in order", async () => {
  const calls = [];
  const postInput = createTerminalInputPoster({
    terminalId: "term 1",
    api: async (endpoint, options) => calls.push({ endpoint, options }),
  });
  const onData = createTerminalInputHandler({
    canInput: () => true,
    onBlocked: () => assert.fail("live terminal input must not be blocked"),
    postInput,
  });
  const reports = ["\x1b[<0;12;34M", "\x1b[<32;13;35M", "\x1b[<0;12;34m"];

  await Promise.all(reports.map(onData));

  assert.deepEqual(calls.map(({ endpoint }) => endpoint), reports.map(() => "/terminals/term%201/input"));
  assert.deepEqual(calls.map(({ options }) => JSON.parse(options.body).body), reports);
});

test("Batch 2: terminal fit is guarded and ResizeObserver is rAF-coalesced", () => {
  const source = read("app.js");
  // safeFit refuses a detached/zero-sized host (fit() during a 0px transition crashes WebGL).
  assert.match(source, /const safeFit = \(\) =>/);
  assert.match(source, /!container\.isConnected/);
  assert.match(source, /container\.clientWidth <= 0 \|\| container\.clientHeight <= 0/);
  // Observer bursts collapse to one rAF.
  assert.match(source, /let roFrame = 0;/);
  assert.match(source, /roFrame = requestAnimationFrame\(/);
  // No raw unguarded fitAddon.fit() outside the safeFit helper / read-only proposeDimensions.
  const rawFits = (source.match(/fitAddon\.fit\(\)/g) || []).length;
  assert.equal(rawFits, 1, "only the single fitAddon.fit() inside safeFit should remain");
});

test("Batch 2: font warm-up runs before term.open", () => {
  const source = read("app.js");
  assert.match(source, /document\.fonts\.load\('13px "Cascadia Code"'\)/);
  const warm = source.indexOf("document.fonts.load('13px");
  const open = source.indexOf("term.open(container)");
  assert.ok(warm > 0 && open > warm, "font warm-up must precede term.open");
});

test("Batch 2: WS half-open watchdog + resume-reconnect wired", () => {
  const source = read("app.js");
  assert.match(source, /WS_CONNECTING_TIMEOUT_MS = 8000/);
  assert.match(source, /readyState === WebSocket\.CONNECTING/, "watchdog force-closes a stuck CONNECTING socket");
  assert.match(source, /function wireRealtimeResumeReconnect\(\)/);
  for (const ev of ['visibilitychange', 'pageshow', 'focus', 'online']) {
    assert.ok(source.includes(`'${ev}'`), `resume reconnect must listen to ${ev}`);
  }
  assert.match(source, /\nwireRealtimeResumeReconnect\(\);/, "resume reconnect must be wired at boot");
});

test("terminal theme follows the dashboard accent + clears WebGL atlas on change", () => {
  const source = read("app.js");
  // Theme is derived (not a hardcoded literal) and stays dark for TUI legibility.
  assert.match(source, /function terminalThemeFromDashboard\(\)/);
  assert.match(source, /theme: terminalThemeFromDashboard\(\)/, "ctor must use the derived theme");
  assert.ok(!/theme: \{ background: '#0b0e13', foreground: '#cdd6f4', cursor: '#51c5b0' \}/.test(source),
    "the hardcoded fixed terminal theme must be gone");
  // Live re-theme clears the WebGL glyph-color atlas (else stale-colored cells).
  assert.match(source, /function refreshActiveTerminalTheme\(\)/);
  assert.match(source, /entry\.webgl\?\.clearTextureAtlas\?\.\(\)/);
  // ...but MUST be change-gated: it runs on the ~15s poll, and an unconditional atlas clear would
  // flicker an open console every tick.
  assert.match(source, /if \(entry\._themeAccent === accent\) return;/,
    "refreshActiveTerminalTheme must no-op when the accent is unchanged (poll-safety)");
  // The webgl addon is stored on the entry so the atlas can be cleared.
  assert.match(source, /webgl: webglAddon/);
  // And the re-theme is wired into the appearance apply/preview paths.
  assert.ok((source.match(/refreshActiveTerminalTheme\(\);/g) || []).length >= 3,
    "re-theme must be wired into save/preview/undo appearance paths");
});

test("mount is supersession-guarded across the font await (no leaked xterm/GL context)", () => {
  const source = read("app.js");
  // A generation token is captured before the font await and re-checked before term.open, so a
  // rapid session switch during an uncached-font load can't leave two live consoles.
  assert.match(source, /const _mountGen = \+\+_consoleMountGen;/);
  assert.match(source, /if \(_mountGen !== _consoleMountGen \|\| !container\.isConnected\)/,
    "mount must bail (disposing its term) if superseded during the font await");
});

test("WS half-open watchdog is per-socket (not a shared global id)", () => {
  const source = read("app.js");
  assert.match(source, /const sock = dashboardSocket;/);
  assert.match(source, /const watchdog = setTimeout\(\(\) => \{\s*if \(sock\.readyState === WebSocket\.CONNECTING\)/,
    "watchdog must act on its own captured socket");
  assert.ok(!/_wsConnectingWatchdog/.test(source), "the shared global watchdog id must be gone");
  // Resume nudge leaves a CONNECTING socket to the watchdog instead of aborting it.
  assert.match(source, /if \(rs === WebSocket\.OPEN \|\| rs === WebSocket\.CONNECTING\) return;/);
});
