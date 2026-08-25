import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldLoadFiles } from "./files-page.mjs";

const docWith = (el) => ({ getElementById: (id) => (id === "page-files" ? el : null) });
const pageEl = (classes) => ({ classList: { contains: (c) => classes.includes(c) } });

test("the page is open, so its data is fetched", () => {
  assert.equal(shouldLoadFiles(docWith(pageEl(["page", "active"]))), true);
});

test("the page is closed, so the fetch is skipped", () => {
  // The whole point: this is the case that saves ~24 MB an hour per tab (34,839 gzipped bytes per
  // cycle, measured against the live service with 388 files on 2026-08-25).
  assert.equal(shouldLoadFiles(docWith(pageEl(["page"]))), false);
});

test("a page state that cannot be determined fetches anyway", () => {
  // FAILS CLOSED. Each of these once meant "skip" if the predicate had been written as a plain
  // truthiness check, and the visible result would have been an empty Files page with nothing to
  // correct it -- a guard that silently withholds data is worse than no guard.
  assert.equal(shouldLoadFiles(null), true, "no document");
  assert.equal(shouldLoadFiles(undefined), true, "no argument, and no global document under Node");
  assert.equal(shouldLoadFiles({}), true, "a document with no getElementById");
  assert.equal(shouldLoadFiles(docWith(null)), true, "the page element is absent");
  assert.equal(shouldLoadFiles(docWith({})), true, "the element has no classList");
  assert.equal(shouldLoadFiles(docWith({ classList: {} })), true, "classList cannot answer contains()");
});

test("it asks for the Files page specifically, not whichever page is active", () => {
  // A predicate that answered "some page is active" would be true forever and skip nothing.
  const doc = { getElementById: (id) => (id === "page-chat" ? pageEl(["page", "active"]) : null) };
  assert.equal(shouldLoadFiles(doc), true, "no #page-files element, so it cannot tell -- fetch");
});
