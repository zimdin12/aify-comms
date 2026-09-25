// The shared older-messages store and the lookup every message action goes through.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { setApiBase } from "./api-client.mjs";
import { findLoadedMessage, messageHistory } from "./message-store.mjs";
import { state } from "./state.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

test("findLoadedMessage finds a live row, a paged-in row, and nothing else", async () => {
  const realFetch = globalThis.fetch;
  setApiBase("");
  globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ messages: [{ id: "old", timestamp: 1 }], truncated: false }) });
  try {
    state.messages = [{ id: "live", timestamp: 9 }];
    await messageHistory.loadOlder(state.messages);
    assert.equal(findLoadedMessage("live")?.id, "live");
    assert.equal(findLoadedMessage("old")?.id, "old", "a row paged in by scrolling back is found too");
    assert.equal(findLoadedMessage("never"), undefined);
    assert.equal(findLoadedMessage(""), undefined, "an empty id matches nothing, not the first id-less row");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("the chat timeline is handed THIS store, so it and the message actions agree", () => {
  // Wiring in app.js, which does not import in Node, so it is read. Two instances would each hold
  // their own pages, and the drawer would again miss every row the timeline paged in.
  const app = fs.readFileSync(path.join(HERE, "app.js"), "utf8");
  assert.match(app, /import \{ messageHistory \} from '\.\/message-store\.mjs';/);
  assert.match(app, /^\s+history: messageHistory,/m);
  assert.doesNotMatch(app, /createMessageHistory\(/, "a second store must not be built here");
});
