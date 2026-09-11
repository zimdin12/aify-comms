import { createChatController } from '../chat.js';
import { messageHtml } from '../chat-render.mjs';
import { setChatView, openChatConversation } from '../chat-click-handlers.mjs';
import { state } from '../state.mjs';
import { MessageHistory } from '../message-history.mjs';
const pause = () => new Promise(r => setTimeout(r, 100));
const assert = (ok, why) => { if (!ok) throw new Error(why); };
const byId = id => document.getElementById(id);
let ctl, calls, bulk;
function setup(extra = {}, history) {
  const old = byId('chat-timeline'); const fresh = old.cloneNode(false); delete fresh.dataset.olderWired; old.replaceWith(fresh);
  calls = []; bulk = [];
  Object.assign(state, { currentPage: 'chat', loaded: true, agents: [{id:'alice',status:'online'}], messages: Array.from({length:12},(_,i)=>({id:`m${i}`,from:'alice',to:'dashboard',body:`Message ${i}`,read:false,timestamp:i+100})), messageCounts:{showing:12,truncated:true} });
  state.chat = {identity:'dashboard',selected:'dm:alice',view:'console',drafts:{},analytics:{},pulse:{},channels:[], ...extra};
  byId('page-chat').hidden = false;
  ctl = createChatController({state,byId,history,markVisibleRead: async (messages, identity) => { calls.push({ids:messages.map(m=>m.id),identity,top:byId('chat-timeline').scrollTop,offsets:messages.map(m=>[...byId('chat-timeline').querySelectorAll('article[data-kind="message"]')].find(el=>el.dataset.id===m.id).getBoundingClientRect().top-byId('chat-timeline').getBoundingClientRect().top)}); },markConversationRead:async()=>bulk.push(1),mountChatConsole:()=>{},refresh:async()=>{}});
  document.onclick = event => { const view = event.target.closest('[data-chat-view]'); if(view) setChatView(view,ctl); };
  ctl.render();
}
async function check(name, fn) { try { await fn(); return {name,ok:true}; } catch(e) { return {name,ok:false,error:e.message}; } }
window.runTests = async () => {
  const results=[];
  results.push(await check('Console to Messenger acknowledges visible only after layout', async()=>{
    setup(); await pause(); assert(calls.length===0,'Console must not read');
    byId('chat-conv-actions').querySelector('[data-chat-view="messenger"]').click(); await pause();
    assert(calls.length>0,'visible Messenger messages were not read');
    const ids=calls.flatMap(c=>c.ids); assert(ids.length<12,'offscreen messages read'); assert(!ids.includes('m0'),'oldest offscreen was read');
    const count=calls.length; ctl.render(); await pause(); assert(calls.length===count,'duplicate acknowledgement on render');
  }));
  results.push(await check('Peek and hidden content never auto-read', async()=>{
    setup({peek:true}); byId('chat-conv-actions').querySelector('[data-chat-view="messenger"]').click(); await pause(); assert(calls.length===0,'Peek read');
    setup(); byId('page-chat').hidden=true; state.chat.view='messenger'; ctl.render(); await pause(); assert(calls.length===0,'hidden read'); byId('page-chat').hidden=false;
  }));
  results.push(await check('rail opening delegates receipts instead of bulk-read', async()=>{
    setup(); state.chat.selected=''; openChatConversation({dataset:{chatOpen:'dm:alice'}},ctl,()=>bulk.push(1)); await pause(); assert(bulk.length===0,'rail opens bulk-read hidden messages');
  }));
  results.push(await check('oldest unread loads history and jumps before acknowledgement', async()=>{
    let pages=0;
    const history=new MessageHistory(async()=>{pages++; await pause(); return {messages:[{id:'old',timestamp:1,from:'alice',to:'dashboard',body:'Oldest unread',read:false}],truncated:false};});
    setup({jumpUnread:true},history);
    byId('chat-conv-actions').querySelector('[data-chat-view="messenger"]').click();
    await new Promise(r=>setTimeout(r,350));
    assert(pages===1,'did not search older history'); assert(calls.some(c=>c.ids.includes('old')),'oldest unread not acknowledged after jump');
    assert(!calls.some(c=>c.ids.includes('m11')),'newest read before oldest jump');
  }));
  results.push(await check('circular help inline and unread accent', async()=>{
    setup({peek:true,view:'messenger'}); ctl.render();
    const help=document.querySelector('.chat-rail-section .chat-rail-why'); assert(help,'help is not beside heading');
    const box=help.getBoundingClientRect(); assert(Math.abs(box.width-box.height)<1,'help is not circular');
    const row=document.querySelector('.chat-msg'); assert(getComputedStyle(row).borderLeftColor===getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || row.classList.contains('chat-msg-unread'),'unread accent marker absent');
  }));
  results.push(await check('safe Markdown and limited HTML render with raw body intact', async()=>{
    const raw='# Heading\n\n**strong** and *emphasis*\n\n- item\n\n```js\n<script>bad()</script>\n```\n\n<details open><summary>More</summary><b>HTML bold</b></details>';
    const m={id:'rich',from:'alice',body:raw,read:false};
    byId('chat-timeline').innerHTML=messageHtml(m);
    assert(document.querySelector('.chat-msg-body h1'),'Markdown heading missing'); assert(document.querySelector('.chat-msg-body strong'),'Markdown bold missing'); assert(document.querySelector('.chat-msg-body b'),'HTML bold missing'); assert(document.querySelector('.chat-msg-body pre code'),'code block missing'); assert(m.body===raw,'raw message mutated');
  }));
  results.push(await check('rich content blocks active HTML, attributes, URLs and clobbering', async()=>{
    const attacks=['<script>window.PWNED=1</script>','<img src=x onerror="window.PWNED=1">','<svg onload="window.PWNED=1"><a href="javascript:alert(1)">x</a></svg>','<iframe srcdoc="<script>parent.PWNED=1</script>"></iframe>','<style>body{display:none}</style>','<a href="jav&#x61;script:alert(1)" onclick="bad()" style="color:red" id="page-chat" data-chat-view="console">bad</a>','[bad](javascript:alert(1))','<a href="data:text/html,bad">bad</a>','<math><mtext><table><mglyph><style><!--</style><img title="--><img src=1 onerror=alert(1)>">'];
    byId('chat-timeline').innerHTML=attacks.map((body,i)=>messageHtml({id:`bad${i}`,body})).join(''); await pause();
    const bodies=[...document.querySelectorAll('.chat-msg-body')];
    assert(!window.PWNED,'script executed');
    for(const body of bodies) for(const el of body.querySelectorAll('*')) {
      assert(!['SCRIPT','STYLE','IFRAME','IMG','SVG','MATH','FORM'].includes(el.tagName),`active tag ${el.tagName}`);
      for(const a of el.attributes) assert(!/^on|^style$|^id$|^data-/i.test(a.name),`unsafe attr ${a.name}`);
      if(el.hasAttribute('href')) assert(/^https?:|^mailto:/i.test(el.getAttribute('href')),`unsafe URL ${el.getAttribute('href')}`);
    }
  }));
  results.push(await check('explicit unread can be acknowledged again when visible', async()=>{
    setup({view:'messenger'}); await pause();
    const id=calls[0]?.ids[0]; assert(id,'no initial receipt');
    state.messages.find(m=>m.id===id).read=false;
    const before=calls.length; ctl.render(); await pause();
    assert(calls.length>before,'permanent receipt cache loses explicitly unread messages');
  }));
  results.push(await check('capped history keeps a visible honest notice after receipt render', async()=>{
    let pages=0;
    const history=new MessageHistory(async()=>({messages:[{id:`older${++pages}`,timestamp:100-pages,from:'alice',to:'dashboard',body:'Older unread',read:false}],truncated:true}));
    setup({view:'messenger',jumpUnread:true},history); await new Promise(r=>setTimeout(r,450));
    assert(pages===20,'automatic history paging must remain bounded');
    assert(!history.exhausted,'cap must not claim exhaustion');
    assert(byId('chat-timeline').textContent.includes('Jump limited to loaded history'),'receipt render erased limited-history notice');
    assert(state.messages.some(m=>m.read===false),'offscreen unread lost');
  }));
  results.push(await check('selection switch during history fetch cannot read the old conversation', async()=>{
    let resolve; const history=new MessageHistory(()=>new Promise(r=>resolve=r));
    setup({view:'messenger',jumpUnread:true},history); await pause();
    state.chat.selected='dm:bob'; ctl.render();
    resolve({messages:[{id:'old-alice',timestamp:1,from:'alice',to:'dashboard',body:'Old',read:false}],truncated:false});
    await new Promise(r=>setTimeout(r,250));
    assert(!calls.some(c=>c.ids.includes('old-alice')),'stale generation acknowledged old peer');
  }));
  results.push(await check('failed oldest-unread search blocks incidental renders and scroll until deliberate retry', async()=>{
    let attempts=0;
    const history=new MessageHistory(async()=>{ if(++attempts===1) throw Error('offline'); return {messages:[{id:'retry-old',timestamp:1,from:'alice',to:'dashboard',body:'Oldest after retry',read:false}],truncated:false}; });
    setup({view:'messenger',jumpUnread:true},history); await pause();
    ctl.render(); byId('chat-timeline').scrollTop=0; byId('chat-timeline').dispatchEvent(new Event('scroll')); await pause();
    assert(calls.length===0,'failed search acknowledged the pre-jump viewport');
    assert(attempts===1,'incidental update retried failed search');
    assert(byId('chat-timeline').textContent.includes('Could not load older messages'),'missing history error');
    byId('chat-timeline').querySelector('[data-messenger-retry]').click(); await pause(); await pause();
    assert(attempts===2,'deliberate retry did not fetch');
    assert(calls.some(c=>c.ids.includes('retry-old')),'retry did not position before receipt');
    assert(!calls.some(c=>c.ids.includes('m11')),'retry read newest before positioning');
  }));
  results.push(await check('failed search survives hidden render and identity change cancels old history', async()=>{
    let reject;
    const history=new MessageHistory(()=>new Promise((_,r)=>reject=r));
    setup({view:'messenger',jumpUnread:true},history); await pause();
    state.chat.identity='other'; ctl.render(); reject(Error('old identity offline')); await pause();
    assert(!calls.some(c=>c.identity==='dashboard'),'old identity received receipt');
    assert(!byId('chat-timeline').textContent.includes('Could not load older messages'),'stale failure leaked into new identity');
    setup({view:'messenger',jumpUnread:true},new MessageHistory(async()=>{throw Error('offline');})); await pause();
    byId('page-chat').hidden=true; ctl.render(); await pause();
    byId('page-chat').hidden=false; ctl.render(); await pause();
    assert(calls.length===0,'hidden transition bypassed failure');
    assert(byId('chat-timeline').querySelector('[data-messenger-retry]'),'failed search recovery disappeared');
  }));
  results.push(await check('mixed failed and held-success receipts retain pending ownership', async()=>{
    const {markVisibleRead}=await import('../message-actions.mjs');
    const realFetch=window.fetch; const requests=[]; const releases=[];
    try {
      setup({view:'console'});
      const old=byId('chat-timeline'); old.replaceWith(old.cloneNode(false));
      state.messages=state.messages.slice(0,2); state.chat.view='messenger';
      window.fetch=(url,opts)=>{ requests.push({url,body:JSON.parse(opts.body)}); return url.includes('/m0/') ? Promise.reject(Error('offline')) : new Promise(r=>releases.push(r)); };
      const reader=createChatController({state,byId,markVisibleRead,mountChatConsole:()=>{}});
      reader.render(); await pause(); reader.render(); byId('chat-timeline').dispatchEvent(new Event('scroll')); await pause();
      assert(requests.some(r=>r.url.includes('/m0/')),'failure arm not exercised');
      assert(requests.filter(r=>r.url.includes('/m1/')).length===1,'held sibling receipt duplicated');
      releases.forEach(r=>r(new Response('{}',{status:200}))); await pause();
      reader.render(); await pause();
      assert(requests.filter(r=>r.url.includes('/m1/')).length===1,'successful sibling duplicated after settlement');
      assert(state.messages[0].read===false && state.messages[1].read===true,'partial receipt state incorrect');
      assert(requests.every(r=>r.body.agentId==='dashboard' && r.body.read===true),'wrong receipt identity');
    } finally { releases.forEach(r=>r(new Response('{}',{status:200}))); window.fetch=realFetch; setup(); }
  }));
  results.push(await check('empty peer retains failed-history recovery until positioned deliberate retry', async()=>{
    let attempts=0;
    const history=new MessageHistory(async()=>{
      if(++attempts===1) throw Error('offline');
      return {messages:Array.from({length:12},(_,i)=>({id:`empty-old${i}`,timestamp:i+1,from:'alice',to:'dashboard',body:`Older peer ${i}`,read:i<3})),truncated:false};
    });
    setup({view:'console',jumpUnread:true},history);
    state.messages.forEach(m=>m.from='bob');
    state.chat.view='messenger'; ctl.render(); await pause();
    const recoveryVisible=()=>{
      const timeline=byId('chat-timeline'), error=timeline.querySelector('[role="alert"]'), retry=timeline.querySelector('[data-messenger-retry]');
      assert(state.messages.length>0 && !timeline.querySelector('article[data-kind="message"]'),'empty-peer discriminator lost');
      assert(error?.textContent.includes('Could not load older messages'),'empty peer hides history error');
      assert(retry && retry.getBoundingClientRect().height>0,'empty peer hides deliberate retry');
      assert(!timeline.textContent.includes('No messages yet'),'loaded window claimed global absence');
      assert(attempts===1 && calls.length===0,'incidental event retried or acknowledged failed search');
    };
    recoveryVisible();
    ctl.render(); byId('chat-timeline').dispatchEvent(new Event('scroll')); await pause(); recoveryVisible();
    byId('page-chat').hidden=true; ctl.render(); await pause();
    assert(attempts===1 && calls.length===0,'hidden event bypassed failed search');
    byId('page-chat').hidden=false; ctl.render(); await pause(); recoveryVisible();
    byId('chat-timeline').querySelector('[data-messenger-retry]').click(); await pause(); await pause();
    assert(attempts===2,'retry did not fetch older peer');
    assert(calls.length>0 && calls[0].ids[0]==='empty-old3','first receipt missed oldest unread peer');
    assert(calls[0].top>0 && Math.abs(calls[0].offsets[0])<2,'receipt preceded oldest-unread positioning');
    assert(!calls.some(c=>c.ids.includes('empty-old11')),'newest offscreen peer acknowledged');
    assert(state.messages.every(m=>m.read===false),'other peer read state changed');
  }));
  results.push(await check('empty peer shows incomplete history at the unchanged automatic cap', async()=>{
    let pages=0;
    const history=new MessageHistory(async()=>({messages:[{id:`other-old${++pages}`,timestamp:100-pages,from:'bob',to:'dashboard',body:'Other peer',read:false}],truncated:true}));
    setup({view:'console',jumpUnread:true},history); state.messages.forEach(m=>m.from='bob');
    state.chat.view='messenger'; ctl.render(); await new Promise(r=>setTimeout(r,450));
    assert(state.messages.length>0 && !byId('chat-timeline').querySelector('article[data-kind="message"]'),'empty-peer cap discriminator lost');
    assert(pages===20 && !history.complete && !history.exhausted,'automatic cap changed or claimed completeness');
    assert(byId('chat-timeline').textContent.includes('Jump limited to loaded history'),'empty peer hides incomplete-history notice');
    assert(!byId('chat-timeline').textContent.includes('No messages yet'),'bounded search claimed global absence');
    ctl.render(); await pause();
    assert(pages===20 && calls.length===0,'incidental render retried or read other peer');
    assert(byId('chat-timeline').textContent.includes('Jump limited to loaded history'),'render erased incomplete-history notice');
  }));
  window.results=results; return results;
};
window.fixture={setup, get controller(){return ctl;},get calls(){return calls;},state};
setup();
