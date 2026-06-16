#!/usr/bin/env node
// Tests for ui.js — toast + asyncAction. The dialog (uiConfirm/uiPrompt) is exercised live
// in the browser; here we cover the DOM-light pieces with a minimal document stub. ui.js
// caches its toast host at module scope (correct: one host, reused), so the stub is installed
// ONCE and tests assert child-count deltas rather than resetting document between tests.
//
// Run: node --test service/new_dashboard/ui.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";

class El {
  constructor(tag = "div") {
    this.tagName = tag; this.className = ""; this.children = []; this.isConnected = false;
    this._text = ""; this.attributes = {};
  }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  setAttribute(k, v) { this.attributes[k] = v; }
  appendChild(child) { this.children.push(child); child.isConnected = true; return child; }
  remove() { this.isConnected = false; }
  addEventListener() {}
  classList = {
    _set: new Set(),
    add: (c) => this.classList._set.add(c),
    remove: (c) => this.classList._set.delete(c),
    contains: (c) => this.classList._set.has(c),
  };
}

const body = new El("body");
globalThis.document = { body, createElement: (tag) => new El(tag), addEventListener() {}, removeEventListener() {} };
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.window = { addEventListener() {} };

const { toast, asyncAction } = await import("./ui.js");

function toastHost() {
  return body.children.find((c) => c.className === "toast-host");
}

test("toast appends toned toasts under a single reused host", () => {
  toast("hello", "ok", { timeout: 0 });
  toast("oops", "error", { timeout: 0 });
  const hosts = body.children.filter((c) => c.className === "toast-host");
  assert.equal(hosts.length, 1, "exactly one toast-host is created and reused");
  const toasts = hosts[0].children;
  assert.ok(toasts.some((t) => t.className.includes("toast-ok") && t.textContent === "hello"));
  assert.ok(toasts.some((t) => t.className.includes("toast-error") && t.textContent === "oops"));
});

test("asyncAction runs the wrapped fn and adds no toast on success", async () => {
  const before = toastHost()?.children.length ?? 0;
  let ran = false;
  await asyncAction(async () => { ran = true; }, "DoThing")();
  assert.equal(ran, true);
  assert.equal(toastHost()?.children.length ?? 0, before, "no toast on success");
});

test("asyncAction toasts (error tone) and never throws when the fn rejects", async () => {
  const before = toastHost()?.children.length ?? 0;
  await asyncAction(async () => { throw new Error("boom"); }, "DoThing")(); // must not throw
  const toasts = toastHost().children;
  assert.equal(toasts.length, before + 1, "exactly one failure toast is added");
  const latest = toasts[toasts.length - 1];
  assert.ok(latest.className.includes("toast-error"));
  assert.ok(latest.textContent.includes("DoThing failed") && latest.textContent.includes("boom"));
});
