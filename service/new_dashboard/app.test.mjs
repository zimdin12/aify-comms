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
  // v0.5.4: pinned the EXACT import list, so every extraction that adds a name to util.js edited this
  // line. What the test cares about is that app.js imports its pure cores from util.js rather than
  // redefining them, not which names exist this week — so it asserts the source module and requires the
  // long-standing three to be among the imported names.
  const utilImport = source.match(/import \{([^}]*)\} from '\.\/util\.js'/);
  assert.ok(utilImport, "app.js must import its pure cores from util.js");
  const utilNames = utilImport[1].split(',').map((n) => n.trim());
  for (const name of ['esc', 'relTime', 'tsMs']) {
    assert.ok(utilNames.includes(name), `${name} must still come from util.js, not be redefined`);
  }
  assert.match(source, /from '\.\/terminal-input\.mjs'/);
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

test("chat composer has NO sticky queue control — queueing is per-message only", () => {
  const html = read("index.html");
  // This test used to assert `#chat-queue` exists and is not `checked`, with the comment "Queue is
  // an explicit operator choice". That intent was right; the mechanism defeated it. The checkbox was
  // never reset after a send and sat inside the collapsed Options disclosure, so one tick queued
  // every LATER message invisibly — the operator hit exactly that on 2026-07-27 ("what does ordinary
  // pressing enter do? ... message was queued"). "Unchecked by default" only ever constrained the
  // FIRST send.
  //
  // So the guarantee is now structural rather than a default: there is no sticky control to leave on.
  // Queue is the second half of the split Send button and passes an explicit per-send flag, which
  // chat.test.mjs pins behaviourally (bare send() never queues; send({queue:true}) does).
  assert.doesNotMatch(html, /id="chat-queue"/,
    "the sticky queue checkbox must stay removed — a per-send choice must not have a persistent mode");
  assert.match(html, /id="chat-send-queue"/, "the explicit Queue half of the split Send button must exist");
  assert.match(html, /type="submit"[^>]*composer-send-main/,
    "Send stays the form's submit action, so Enter is an ordinary send");
});

// RETIRED: "click handling processes mode switch before session row selection".
//
// It compared two `indexOf` results in app.js. The claim is real — the chip is nested inside a
// selectable row, so whichever branch is checked first claims the click — but the delegated listener
// moved to click-dispatch.mjs in v0.5.4, and `click-dispatch.test.mjs` now DISPATCHES a click at a
// target matching BOTH selectors and checks which branch claimed it, which is the only way to test an
// ordered chain. It also asserts the other direction (a plain row still selects), without which the
// first assertion passes against a dispatcher that dropped row selection entirely.

test("Work Loop board view: toggle, renderer, and card reuse are wired", () => {
  const html = read("index.html");
  const source = read("app.js");
  const styles = read("styles.css");
  // The List/Board toggle exists in the Work Loop toolbar.
  assert.match(html, /data-contract-view="list"/, "List toggle button must exist");
  assert.match(html, /data-contract-view="board"/, "Board toggle button must exist");
  // The column model and the renderer MOVED to work-loop-panels.mjs in v0.5.4, and
  // work-loop-panels.test.mjs now asserts them by behaviour: each column predicate is called with the
  // states it must and must not claim, an unrecognised state lands in Other rather than vanishing, and an
  // empty terminal column is hidden while the always-on ones render. The regexes here matched the
  // predicates as TEXT, so a column model listing the wrong states would have satisfied them exactly as
  // well — and they broke when the code moved though nothing about the board changed.
  //
  // What stays is the wiring only app.js and its siblings can prove.
  // `renderContracts` moved to work-loop-actions.mjs in v0.5.4, so the branch is asserted where it now
  // lives. It stays a source check rather than becoming a behavioural one because what it claims is a
  // WIRING fact — the renderer reads the PERSISTED view rather than a local default — and the panel it
  // renders into is asserted by work-loop-panels.test.mjs.
  assert.ok(read("work-loop-actions.mjs").includes("state.contractView === 'board'"),
    "renderContracts must branch on the persisted view");
  // The click handler is scoped to the button (must not swallow card actions — same lesson as work-view).
  // The delegated listener moved to click-dispatch.mjs in v0.5.4. The scoping claim is asserted there
  // both ways — a bare-attribute target must NOT switch the view, and the button form must — so this is
  // now just the wiring check that the branch exists at all.
  assert.match(read("click-dispatch.mjs"), /event\.target\.closest\('button\[data-contract-view\]'\)/,
    "contract-view handler must be scoped to button[data-contract-view]");
  // The persistence MOVED to work-loop-panels.mjs with `applyContractView` in v0.5.4, and
  // work-loop-panels.test.mjs now asserts it by behaviour: every input is normalised to exactly `board`
  // or `list`, the SAME value is what reaches storage, and a refusing storage still switches the view.
  // The regex here matched the setItem call as TEXT — it would have passed just as happily on a handler
  // that persisted the raw attribute, which is the bug it looked like it was guarding.
  // CSS for the board columns is present.
  assert.match(styles, /\.contract-list\.is-board/, "is-board container style must exist");
  assert.match(styles, /\.contract-board-col/, "board column style must exist");
});

test("xterm remount guard checks container identity, not just terminal id", () => {
  // The xterm mount moved to `xterm-mount.mjs` in v0.5.4 — 356 lines, the largest function
  // in app.js. These are behaviour ASSERTIONS over source text (a guard's shape, an option's
  // value), so they stay source checks; they now read the file that holds the code.
  const source = read("xterm-mount.mjs");
  assert.match(source,
    /state\.activeXterm\.container === container[\s\S]*container\.isConnected !== false/,
    "mountXtermForTerminal must remount when render recreated the host container");
});

test("Continue in CLI is wired from the drawer to the command builder", () => {
  // TWO MOVES, one pattern. `continueCliDetails` went to cli-resume.mjs in 2026-07-28 so it could be
  // unit-tested; `openAgentDrawer` — its only caller — went to agent-drawer.mjs in v0.5.4. Each time the
  // behavioural claims went to the new module's own tests and the WIRING stayed here.
  //
  //   cli-resume.test.mjs   : the handle can come from the SESSION when the agent row omits it;
  //                           no handle yields no command but an explanatory reason
  //   agent-drawer.test.mjs : the block renders unconditionally, and stays shell-neutral
  const cliResume = read("cli-resume.mjs");
  assert.match(cliResume, /session\?\.sessionHandle \|\| session\?\.session_handle/,
    "a session handle must not disappear just because the agent list omits it");
  const drawer = read("agent-drawer.mjs");
  assert.match(drawer, /continueCliDetails\(agent, session\)/,
    "the drawer must pass its linked session to the command builder, not just the agent row");
  assert.ok(!read("app.js").includes("aify-comms.cmd"),
    "copyable dashboard commands should be shell-neutral; PowerShell resolves the shim itself");
});

test("the Continue-in-CLI command block is actually styled", () => {
  // Reported 2026-07-28: "that cli command is just placed there, no padding, ugly". None of
  // .agent-drawer-cli / .cli-cmd-row / .cli-cmd existed in the stylesheet at all.
  const styles = read("styles.css");
  for (const cls of [".agent-drawer-cli", ".agent-drawer-subhead", ".cli-cmd-row", ".cli-cmd"]) {
    assert.ok(styles.includes(cls), `${cls} must be styled, not rendered bare`);
  }
  assert.match(styles, /\.cli-cmd\s*\{[^}]*padding:/s, "the command box needs padding — that was the complaint");
});

// RETIRED: "automatic console resync never self-excites a PTY resize loop".
//
// It matched three lines of `resyncActiveConsole` as text — the signature, `if (forceRepaint &&
// entry.ownsPty)`, and `entry.lastSeq = Math.max(`. All three are real claims and none could fail on
// the behaviour it named: `Math.max` present with its arguments swapped passes, and so does an
// `ownsPty` guard that has been inverted.
//
// `console-actions.test.mjs` asserts them by driving the function. The re-entrancy guard is exercised
// by starting two resyncs and counting fetches — the 153↔154-cols flicker loop this test is named
// after — and also checked for CLEARING afterwards and on the failure path, since a leaked guard makes
// the console permanently unrecoverable, which is worse than the flicker. The sequence floor is
// checked in both directions plus a non-numeric snapshot seq. The `ownsPty` guard is checked by
// confirming no resize is sent for a PTY the pane does not own.

test("managed PTY keeps raw terminal semantics and ordered input", () => {
  // The xterm mount moved to `xterm-mount.mjs` in v0.5.4 — 356 lines, the largest function
  // in app.js. These are behaviour ASSERTIONS over source text (a guard's shape, an option's
  // value), so they stay source checks; they now read the file that holds the code.
  const source = read("xterm-mount.mjs");
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
  // The xterm mount moved to `xterm-mount.mjs` in v0.5.4 — 356 lines, the largest function
  // in app.js. These are behaviour ASSERTIONS over source text (a guard's shape, an option's
  // value), so they stay source checks; they now read the file that holds the code.
  const source = read("xterm-mount.mjs");
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
  // The xterm mount moved to `xterm-mount.mjs` in v0.5.4 — 356 lines, the largest function
  // in app.js. These are behaviour ASSERTIONS over source text (a guard's shape, an option's
  // value), so they stay source checks; they now read the file that holds the code.
  const source = read("xterm-mount.mjs");
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
  const source = read("xterm-mount.mjs");
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
  const source = read("xterm-mount.mjs");
  assert.match(source, /document\.fonts\.load\('13px "Cascadia Code"'\)/);
  const warm = source.indexOf("document.fonts.load('13px");
  const open = source.indexOf("term.open(container)");
  assert.ok(warm > 0 && open > warm, "font warm-up must precede term.open");
});

test("the realtime socket is INITIALISED BEFORE IT IS CONNECTED, and resume is wired at boot", () => {
  // What used to be here: two tests that grepped app.js for `WS_CONNECTING_TIMEOUT_MS = 8000`,
  // `readyState === WebSocket.CONNECTING`, `const sock = dashboardSocket;` and the four resume event
  // names. All of it moved to realtime-socket.mjs in v0.5.4, and all of it is now covered by
  // realtime-socket.test.mjs, which DRIVES a fake WebSocket through the states rather than reading the
  // source: the watchdog force-closes a stuck CONNECTING socket and leaves a healthy one, each socket
  // arms its own watchdog, the backoff grows and caps at 30s, a healthy open resets it, and a hidden
  // visibilitychange does not reconnect. Not one of those could fail a source match.
  //
  // ONE property genuinely belongs to app.js and cannot move: the ORDER of the boot calls. The module
  // takes its dependencies through `initRealtimeSocket`, so connecting first would run the first
  // socket — and, until the first reconnect, every event it delivers — against no-op defaults. The
  // page would look connected and do nothing.
  const source = read("app.js");
  const init = source.indexOf("initRealtimeSocket({");
  const connect = source.search(/^connectRealtimeSocket\(\);$/m);
  const wire = source.search(/^wireRealtimeResumeReconnect\(\);$/m);
  assert.ok(init !== -1, "app.js must supply the socket's dependencies");
  assert.ok(connect !== -1, "app.js must open the socket at boot");
  assert.ok(wire !== -1, "resume reconnect must be wired at boot");
  assert.ok(init < connect, "initRealtimeSocket must run BEFORE the first connect");
});

test("terminal theme wiring stays in app.js — the derivation itself moved", () => {
  const source = read("xterm-mount.mjs");
  // SPLIT in v0.5.4. `terminalThemeFromDashboard` and `refreshActiveTerminalTheme` moved to
  // settings-panel.mjs and are now covered by settings-panel.test.mjs, which CALLS them — including the
  // poll-safety gate (an unchanged accent must not clear the WebGL atlas, or an open console flickers
  // every ~15s tick). This test used to match those functions' source text, which proves a line was
  // written and nothing about whether it works, and it broke when they moved even though the behaviour
  // did not change.
  //
  // What remains here is WIRING, which is what a source check is genuinely for while app.js itself
  // cannot be imported: that the terminal is constructed with the derived theme rather than a literal,
  // that the webgl addon is kept on the entry so its atlas is reachable, and that the re-theme is
  // called from the save/preview/undo appearance paths.
  // THIS TEST NOW SPANS TWO FILES, which is the honest shape: the mount moved to xterm-mount.mjs in
  // v0.5.4, but the appearance paths that re-theme a live console stayed in app.js. Reading one file for
  // both halves is what made it fail on a pure relocation.
  assert.match(source, /theme: terminalThemeFromDashboard\(\)/, "ctor must use the derived theme");
  assert.ok(!/theme: \{ background: '#0b0e13', foreground: '#cdd6f4', cursor: '#51c5b0' \}/.test(source),
    "the hardcoded fixed terminal theme must be gone");
  assert.match(source, /webgl: webglAddon/);

  // A COUNT WAS THE WRONG ASSERTION and this slice proved it. It required >= 3 occurrences in app.js;
  // the poll cycle moved to refresh-cycle.mjs in v0.5.4 taking one of the three with it, and the count
  // went red on a relocation that changed no behaviour. Worse, it could never have failed on the defect
  // it names: three calls all wired into the SAME path would satisfy it, and the path that actually
  // matters — an operator saving an accent while a console is open — would still be unwired.
  //
  // So each path is asserted where it now lives, by what it is next to.
  const app = read("app.js");
  const paths = [
    [app, /state\.settings = res && typeof res === 'object'[\s\S]{0,200}?refreshActiveTerminalTheme\(\);/,
      "SAVE: persisting new settings must re-theme a live console"],
    [app, /\/\/ undo any live appearance preview\s*\n\s*refreshActiveTerminalTheme\(\);/,
      "UNDO: reverting an unsaved preview must re-theme back"],
    [read("refresh-cycle.mjs"), /applyTheme\(state\.settings\);[\s\S]{0,120}?refreshActiveTerminalTheme\(\);/,
      "POLL: server-stored appearance arriving on a tick must re-theme (moved out of app.js in v0.5.4)"],
  ];
  for (const [source, pattern, why] of paths) assert.match(source, pattern, why);
});

test("mount is supersession-guarded across the font await (no leaked xterm/GL context)", () => {
  const source = read("xterm-mount.mjs");
  // A generation token is captured before the font await and re-checked before term.open, so a
  // rapid session switch during an uncached-font load can't leave two live consoles.
  assert.match(source, /const _mountGen = \+\+_consoleMountGen;/);
  assert.match(source, /if \(_mountGen !== _consoleMountGen \|\| !container\.isConnected\)/,
    "mount must bail (disposing its term) if superseded during the font await");
});

test("nothing in app.js still reaches for the socket that left it", () => {
  // The per-socket watchdog claim this replaces is now asserted by CALLING the code:
  // realtime-socket.test.mjs opens two sockets, drives the first to CLOSED, and checks the SUCCESSOR's
  // watchdog still fires — which is the failure a shared global id produced and which
  // `assert.match(source, /const sock = dashboardSocket;/)` could not have detected either way.
  //
  // What is worth checking in app.js after a move is the opposite: that no reader was left behind. A
  // stale `dashboardSocket` reference here would be an undefined variable that only throws on the code
  // path that touches it, which for a socket is a laptop-sleep or a deploy — never in a test run.
  const source = read("app.js");
  const live = source
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
  for (const name of ["dashboardSocket", "_wsReconnectAttempts", "_wsResumeNudgeAt", "WS_CONNECTING_TIMEOUT_MS"]) {
    assert.doesNotMatch(live, new RegExp(`\\b${name}\\b`), `${name} moved to realtime-socket.mjs; app.js must not reference it`);
  }
});

// The agent-edit runtime choices were asserted here by pulling the `runtimeOptions` expression out of
// app.js with a regex and eval'ing it. `openAgentEditForm` moved to inspector-forms.mjs in v0.5.4, and
// inspector-forms.test.mjs now RENDERS the form and reads the options out of the markup: that hermes is
// offered under its canonical identifier, and that an unrecognised runtime is added to the list rather
// than silently reset — which would change an agent's runtime as a side effect of opening a form.
//
// Worth noting what the old test had already been through: it once asserted whitespace and went red
// locally while green in CI, because the working tree was CRLF and git normalises to LF. Rendering the
// output has no whitespace to be wrong about.

test("the agent-level stop is ROUTED by app.js — the half that could not move", () => {
  // The rest of this test moved to agent-session-actions.test.mjs when `stopAgentWorker` did, and it is
  // strictly stronger there. The confirmation is now checked in BOTH directions (answering No sends
  // nothing) where the regex only proved the string `uiConfirm(` appeared somewhere in the function.
  // The await-before-render ordering — which shipped as a real bug: the drawer painted from PRE-stop
  // state and offered a live "Stop worker" button for a worker already gone — is now observed by
  // recording the actual sequence of effects, where the regex compared two string indices and would
  // have been satisfied by the words appearing in a comment in that order.
  //
  // What stays is the wiring only app.js holds: the delegated click handler must route the button to
  // something. Without it the control is inert and every assertion over there is about code no button
  // reaches.
  // The delegated listener moved to click-dispatch.mjs in v0.5.4; the routing claim moved with it.
  const source = read("click-dispatch.mjs");
  assert.match(source, /data-agent-stop-worker\]/, "the click handler must route the button");
  assert.match(source, /stopAgentWorker\(/, "…to the action that performs the teardown");
});

test("the chat controller is wired to the drawer's selection sync", () => {
  // `syncInspectorToSelection` moved to agent-drawer.mjs in v0.5.4. Its five outcomes — closed inspector,
  // non-agent drawer, non-DM selection, same agent, different agent — are each asserted in
  // agent-drawer.test.mjs by driving the function, which is strictly more than the regexes here could do:
  // they matched the branch conditions as TEXT and would have passed on an inverted comparison.
  //
  // The wiring is the part that lives here and nowhere else. Without it the sync is never called and the
  // drawer goes stale exactly as it did before the feature existed.
  assert.match(read("app.js"), /onSelectionChange: \(\) => syncInspectorToSelection\(\)/);
});

test("chat controller notifies on every selection change", () => {
  const source = read("chat.js");
  assert.match(source, /deps\.onSelectionChange === 'function'/, "hook must be optional for tests");
  // open(), the analytics early-return, and close() all change the selection — all three must fire.
  const calls = source.match(/onSelectionChange\(\);/g) || [];
  assert.ok(calls.length >= 3,
    `all selection-changing paths must notify (open, analytics switch, close); found ${calls.length}`);
});

// REMOVED (2026-07-26): "doctor rejects a FUTURE lastSeen instead of greening a dead bridge".
//
// It asserted the property by regex-matching the SOURCE of ../../mcp/stdio/doctor.js for
// `function envIsOnline` containing `age >= 0`. Two problems, and the second is why it is gone
// rather than repointed:
//   1. it broke the moment the predicates moved to mcp/stdio/doctor-predicates.js — a pure
//      refactor with no behaviour change turned the suite red, which is the signature of a test
//      coupled to layout instead of behaviour;
//   2. a regex over source text cannot fail when the LOGIC is wrong, only when the TEXT moves. It
//      would have passed just as happily on `const age = ...; return age >= 0 || true`.
//
// The property is now covered behaviourally and strictly more strongly, in the suite that owns the
// module: mcp/stdio/tests/doctor-env-predicates.test.js exercises a future lastSeen at +1min,
// +1day and +1year, plus the age-0 and exact-bound boundaries, by CALLING the predicate with an
// injected clock. Nothing was weakened to make this suite green — see also the arity-trap test
// there, which caught a real false RED this file's regex could never have seen.
