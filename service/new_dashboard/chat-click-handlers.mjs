// Click-handler bodies for the chat surface.
//
// Every one of these was unreachable by any test while it lived inside app.js's 390-line delegated click
// handler. They are small, and each does two or three things whose omission is invisible on the day: a
// selection that never re-renders, a view switch that leaks the inline terminal.
//
// NOT IN chat.js, deliberately. That module takes `state` and `byId` INJECTED through
// `createChatController(deps)` rather than importing them, and these bodies reference `state` directly —
// importing it there to host them would break the one design decision chat.js makes. These are click
// handlers, not part of the controller, so they get their own home.
//
// `chatController` arrives as a PARAMETER for the same reason it cannot move: app.js builds it with
// app.js-local callbacks. Passing it leaves every body byte-identical to the branch it left — the name it
// reads is a parameter now instead of a module-scope const.

import { messageId } from './record-fields.mjs';
import { state } from './state.mjs';
import { byId } from './ui.js';
import { disposeActiveXterm } from './xterm-lifecycle.mjs';

export function openChatReply(chatReply, chatController) {
  const msg = state.messages.find((m) => messageId(m) === chatReply.dataset.chatReply);
  if (msg) {
    state.chat.replyTo = { id: messageId(msg), from: msg.from || 'unknown', subject: msg.subject || '', preview: msg.body || msg.preview || '', conversationKey: state.chat.selected };
    chatController.renderConversation();
    byId('chat-composer-body')?.focus();
  }
}

export function openChatConversation(chatOpen, chatController, markConversationRead) {
  const key = chatOpen.dataset.chatOpen;
  // Click-again gesture: re-clicking the already-open conversation closes it back to the
  // chat overview (fleet stats + most-active). Per-agent analytics stays reachable via the
  // explicit "Analytics" action button. (Operator: re-click open chat → close + show stats.)
  if (key === state.chat.selected && !state.chat.analytics.agent) {
    chatController.close();
  } else {
    chatController.open(key);
    // Opening a DM marks its messages read — UNLESS Peek mode is on (watch without marking).
    if (!state.chat.peek && key.startsWith('dm:')) markConversationRead(key.slice('dm:'.length), { quiet: true });
  }
}

export function setPulseWindow(pulseWindow, chatController) {
  const mins = Number(pulseWindow.dataset.pulseWindow) || 60;
  if (mins !== state.chat.pulse.window) {
    state.chat.pulse.window = mins;
    chatController.refreshPulse(true);
  }
}

export function setChatView(chatView, chatController) {
  const next = chatView.dataset.chatView === 'console' ? 'console' : 'messenger';
  if (next !== state.chat.view) {
    state.chat.view = next;
    if (next === 'messenger') disposeActiveXterm(); // free the inline terminal when leaving Console
    chatController.renderConversation();
  }
}
