// The dashboard's ONE store of older messages, and the lookup every message action goes through.
//
// WHY A SHARED INSTANCE. The chat timeline shows the live poll window PLUS whatever the operator paged
// in by scrolling back (`MessageHistory.combined`). Until v0.7 only the chat controller held the paging
// store, so every other reader of "a message the operator can see" -- the details drawer, Write reply,
// Mark read, Unsend -- searched `state.messages` alone and could not find a paged-in row. In a busy DM
// that is most of the conversation: 43 of 137 messages sat inside the live window when this was
// measured on 2026-09-05. One instance, imported by all of them, makes the timeline and the actions
// agree about what exists.
//
// `api` is imported here rather than in message-history.mjs, which stays network-free for its tests.

import { api } from './api-client.mjs';
import { createMessageHistory } from './message-history.mjs';
import { messageIdOf } from './record-fields.mjs';
import { state } from './state.mjs';

export const messageHistory = createMessageHistory(api);

/** A message the timeline can show: from the live window, or paged in by scrolling back. */
export function findLoadedMessage(id) {
  const wanted = String(id ?? '');
  if (!wanted) return undefined;
  return messageHistory.combined(state.messages).find((m) => messageIdOf(m) === wanted);
}
