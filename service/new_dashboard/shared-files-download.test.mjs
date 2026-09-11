import assert from 'node:assert/strict';
import test from 'node:test';
import * as files from './shared-files.mjs';
import { setApiBase, setOperatorKey } from './api-client.mjs';
import { state } from './state.mjs';

test('rendered Download uses authenticated bytes, cleans blobs, preserves drafts on failure', async () => {
  const saved = new Map(['document','fetch','localStorage','requestAnimationFrame','setTimeout'].map(k => [k, globalThis[k]]));
  const create = URL.createObjectURL, revoke = URL.revokeObjectURL;
  const host = {}, draft = {value:'unsent'}, clicks = [], revoked = [], toasts = [];
  const node = () => ({children:[],classList:{add(){},remove(){}},setAttribute(){},addEventListener(){},appendChild(n){this.children.push(n);toasts.push(n);},remove(){},click(){clicks.push({href:this.href,name:this.download});}});
  let status = 200, seen;
  globalThis.document = {getElementById:id=>id==='files-list'?host:id==='chat-composer-body'?draft:null,createElement:node,body:{appendChild(n){toasts.push(n);}}};
  globalThis.requestAnimationFrame = fn=>fn();
  globalThis.localStorage = {getItem:()=> 'synthetic-service'};
  globalThis.fetch = async (url,options) => {seen={url,...options};return new Response('payload',{status});};
  globalThis.setTimeout = fn=>{fn();return 0;};
  URL.createObjectURL = b=>{assert.equal(b.size,7);return 'blob:owned';};
  URL.revokeObjectURL = u=>revoked.push(u);
  setApiBase('https://owned.invalid/api/v1');setOperatorKey('synthetic-operator');
  try {
    const pasted = {value:'unsent',focus(){},dispatchEvent(){}};
    await files.uploadPastedImage(new Blob(['image'],{type:'image/png'}),pasted);
    assert(toasts.some(n=>n.textContent?.includes('service browser login')));
    assert.doesNotMatch(pasted.value,/synthetic-/);
    state.files=[{name:'a /&.txt',size:7}];files.renderFiles();
    assert.match(host.innerHTML,/data-file-download="a \/&amp;.txt"/);
    assert.equal(typeof host.onclick,'function');
    const event={target:{closest:()=>({dataset:{fileDownload:'a /&.txt'}})},stopPropagation(){}};
    await host.onclick(event);
    assert.equal(seen.url,'https://owned.invalid/api/v1/shared/a%20%2F%26.txt');
    assert.equal(seen.headers['X-API-Key'],'synthetic-service');
    assert.equal(seen.headers['X-Aify-Operator-Key'],'synthetic-operator');
    assert.equal(seen.redirect,'error');
    assert.deepEqual(clicks,[{href:'blob:owned',name:'a /&.txt'}]);assert.deepEqual(revoked,['blob:owned']);
    status=500;await host.onclick(event);
    assert.equal(clicks.length,1);assert.equal(draft.value,'unsent');
    assert(toasts.some(n=>n.textContent?.includes('Download failed:')));
  } finally {
    for(const [k,v] of saved)if(v===undefined)delete globalThis[k];else globalThis[k]=v;
    URL.createObjectURL=create;URL.revokeObjectURL=revoke;setOperatorKey('');
  }
});
