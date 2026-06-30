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
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (name) => fs.readFileSync(path.join(__dirname, name), "utf8");

test("app.js loads as an ES module (Phase 0.1) and imports the extracted pure cores", () => {
  const html = read("index.html");
  assert.match(html, /<script type="module" src="\/assets\/app\.js">/, "index.html must load app.js as a module");
  const source = read("app.js");
  assert.match(source, /import \{ esc, relTime, tsMs \} from '\.\/util\.js'/);
  assert.match(source, /from '\.\/status\.js'/);
  assert.match(source, /from '\.\/console-chooser\.js'/);
  // The extracted definitions must be GONE from app.js (no duplicate source of truth).
  assert.ok(!/\nconst STATUS_KINDS = \{/.test(source), "STATUS_KINDS must live only in status.js");
  assert.ok(!/\nfunction chooseSessionConsoleWidget\(/.test(source), "chooser must live only in console-chooser.js");
});

test("styles.css keeps ready internal: no separate ready dot, .status-ready text alias stays", () => {
  const styles = read("styles.css");
  assert.ok(!/\.status-dot\.ready\b/.test(styles), "no separate ready dot; ready is internal");
  assert.ok(/\.status-ready\b/.test(styles), ".status-ready stays as an old-cache text alias");
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

test("xterm remount guard checks container identity, not just terminal id", () => {
  const source = read("app.js");
  assert.match(source,
    /state\.activeXterm\.container === container[\s\S]*container\.isConnected !== false/,
    "mountXtermForTerminal must remount when render recreated the host container");
});
