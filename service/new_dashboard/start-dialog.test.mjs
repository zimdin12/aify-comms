// The Start dialog: one search box over every agent, and the one action that fits each (v0.7.7).
//
// The operator: "starting agent maybe should open something that has a search". The dashboard had
// five ways to start or spawn and none searched. These tests hold the rows and the actions; the
// rule for offering Start is `startOffer`'s, tested beside it.
//
// NOT REACHABLE HERE: `openStartDialog` and `initStartDialog` are the DOM half (focus, keys, the
// overlay), which needs a browser. `fixtures/ux-browser-check.mjs` drives both in Chromium, a
// manual run described in `fixtures/README.md`.

import assert from "node:assert/strict";
import test from "node:test";

import { isStartHotkey, startDialogAct, startDialogRows } from "./start-dialog.mjs";

const AGENTS = [
  { id: "sc-manager", status: "stopped", sessionMode: "managed", role: "manager", runtime: "claude-code" },
  { id: "sc-coder", status: "working", sessionMode: "managed", runtime: "codex" },
  { id: "pc-manager", status: "available", sessionMode: "resident" },
  { id: "boot", status: "starting", sessionMode: "managed" },
  { id: "dashboard", status: "online" },
];

test("every agent is a row, with the action that fits it", () => {
  const rows = startDialogRows(AGENTS, "");
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.ok(!byId.dashboard, "the dashboard's own identity is not an agent to start");
  assert.equal(byId["sc-manager"].action, "start");
  assert.equal(byId["sc-coder"].action, "console", "a live agent opens its console");
  assert.equal(byId["pc-manager"].action, "none");
  assert.match(byId["pc-manager"].why, /resident/, "a resident says why it is not offered Start");
  assert.equal(byId.boot.action, "none");
  assert.match(byId.boot.why, /starting/);
});

test("the search matches name, role, runtime and status", () => {
  assert.deepEqual(startDialogRows(AGENTS, "MANAGER").map((r) => r.id), ["pc-manager", "sc-manager"]);
  assert.deepEqual(startDialogRows(AGENTS, "codex").map((r) => r.id), ["sc-coder"]);
  assert.deepEqual(startDialogRows(AGENTS, "stopped").map((r) => r.id), ["sc-manager"]);
});

test("a name that matches no agent offers Create, and only then", () => {
  assert.deepEqual(startDialogRows(AGENTS, "  new-coder "), [{ id: "new-coder", status: "", action: "create", why: "" }]);
  assert.ok(!startDialogRows(AGENTS, "sc").some((r) => r.action === "create"), "CONTROL: a query with matches offers no Create");
});

function doubles({ answer = { ok: true }, fail = null } = {}) {
  const calls = [];
  return {
    calls,
    post: async (id) => { calls.push(["post", id]); if (fail) throw new Error(fail); return answer; },
    openConsole: (id) => calls.push(["console", id]),
    openCreate: (id) => calls.push(["create", id]),
    close: () => calls.push(["close"]),
    say: (text) => calls.push(["say", text]),
    started: (text) => calls.push(["started", text]),
  };
}

test("Enter on a startable agent starts it through the control route and closes", async () => {
  const d = doubles();
  await startDialogAct({ id: "sc-manager", action: "start" }, d);
  assert.deepEqual(d.calls.map((c) => c[0]), ["say", "post", "started", "close"]);
  assert.equal(d.calls[1][1], "sc-manager");
});

test("a refusal from the route is shown in the dialog, which stays open", async () => {
  const d = doubles({ fail: "no environment bridge is available to run it" });
  await startDialogAct({ id: "sc-manager", action: "start" }, d);
  assert.ok(d.calls.some(([k, t]) => k === "say" && /no environment bridge/.test(t)), JSON.stringify(d.calls));
  assert.ok(!d.calls.some(([k]) => k === "close"), "a refused start closed the dialog and hid the reason");
});

test("an agent already running is said so, not reported as started", async () => {
  const d = doubles({ answer: { ok: true, alreadyRunning: true } });
  await startDialogAct({ id: "sc-manager", action: "start" }, d);
  assert.ok(d.calls.some(([k, t]) => k === "started" && /already running/.test(t)));
});

test("a live agent opens its console, Create opens the spawn form, and a refused row explains", async () => {
  const live = doubles();
  await startDialogAct({ id: "sc-coder", action: "console" }, live);
  assert.deepEqual(live.calls, [["close"], ["console", "sc-coder"]]);
  const create = doubles();
  await startDialogAct({ id: "new-coder", action: "create" }, create);
  assert.deepEqual(create.calls, [["close"], ["create", "new-coder"]]);
  const none = doubles();
  await startDialogAct({ id: "pc-manager", action: "none", why: "resident: …" }, none);
  assert.deepEqual(none.calls, [["say", "pc-manager: resident: …"]]);
});

test("Ctrl+K opens it, except inside a terminal, where it is the shell's kill-line", () => {
  const key = (target, extra = {}) => ({ key: "k", ctrlKey: true, target, ...extra });
  const page = { closest: () => null };
  const terminal = { closest: (sel) => (sel.includes(".xterm") ? {} : null) };
  assert.equal(isStartHotkey(key(page)), true);
  assert.equal(isStartHotkey(key(terminal)), false, "Ctrl+K was taken from a console");
  assert.equal(isStartHotkey(key(page, { shiftKey: true })), false);
  assert.equal(isStartHotkey({ key: "k", target: page }), false, "CONTROL: a bare k is typing");
});
