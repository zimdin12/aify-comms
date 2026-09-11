import test from 'node:test';
import assert from 'node:assert/strict';
import { persistChatPrefs } from './chat-prefs.mjs';
import { state } from './state.mjs';
import { messageHtml } from './chat-render.mjs';
test('oldest-unread preference is persisted explicitly', () => {
  const prior = globalThis.localStorage;
  let saved;
  globalThis.localStorage = {setItem:(k,v)=>{saved=JSON.parse(v);}};
  try { state.chat.jumpUnread=true; persistChatPrefs(); assert.equal(saved.jumpUnread,true); }
  finally { globalThis.localStorage=prior; delete state.chat.jumpUnread; }
});
test('renderer without DOM retains escaped plain fallback and raw storage',()=>{
  const m={id:'plain',body:'<script>bad()</script> **raw**'};
  const html=messageHtml(m);
  assert.ok(html.includes('&lt;script&gt;bad()&lt;/script&gt; **raw**'));
  assert.equal(m.body,'<script>bad()</script> **raw**');
});
