import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {spawn,execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const rootEvidence='C:/Users/Administrator/AppData/Local/Temp/aify-v062-review';
const base=pathToFileURL(rootEvidence+'/b28-source/mcp/stdio/').href;
const {keyForEndpoint}=await import(base+'registry-credential.mjs');
const {readCredentialFile,writeCredentialFile}=await import(pathToFileURL(rootEvidence+'/env-55b-source/lib/credential-fs.mjs'));
const home=fs.mkdtempSync(path.join(os.tmpdir(),'b28-synthetic-'));
const store=path.join(home,'.aify','credentials'),ref='review.key',fake='SYNTHETIC-REVIEW-ONLY';
assert.equal((await writeCredentialFile({root:store,ref,value:fake})).ok,true);
const registry=path.join(home,'.aify','services.json');
const setRegistry=url=>fs.writeFileSync(registry,JSON.stringify({services:{'aify-comms':{endpoint:url,credentialRef:ref}}}));
const getKey=()=>keyForEndpoint({env:{},homeDir:home,readFile:fs.readFileSync,join:path.join,endpoint:'http://127.0.0.1:12345'}).key;
setRegistry('http://127.0.0.1:12345');
for(const state of ['private','Everyone','restored']){
 if(state==='Everyone')execFileSync('icacls.exe',[path.join(store,ref),'/grant','*S-1-1-0:(R)'],{stdio:'pipe'});
 if(state==='restored')execFileSync('icacls.exe',[path.join(store,ref),'/remove:g','*S-1-1-0'],{stdio:'pipe'});
 const owner=await readCredentialFile({root:store,ref});const accepted=getKey()===fake;
 assert.equal(accepted,state!=='Everyone');assert.equal(owner.state,state==='Everyone'?'CREDENTIAL_INSECURE':'ok');
 console.log(JSON.stringify({case:'real-acl',state,ownerState:owner.state,ownerDetail:owner.detail,commsAccepted:accepted}));
}
const servers=[];
async function receiver(status,location=''){const requests=[];const server=http.createServer((req,res)=>{requests.push({url:req.url,keyMatches:req.headers['x-api-key']===fake,hasKey:Boolean(req.headers['x-api-key'])});res.writeHead(status,{'Content-Type':'application/json',...(location?{Location:location}: {})});res.end('{}');});await new Promise(r=>server.listen(0,'127.0.0.1',r));servers.push(server);return {url:`http://127.0.0.1:${server.address().port}`,requests};}
const good=await receiver(200),foreign=await receiver(200),broken=await receiver(503),redirect=await receiver(302,foreign.url+'/redirect-target');
const cleanEnv={};for(const k of ['PATH','SystemRoot','SYSTEMROOT','WINDIR','TEMP','TMP','USERNAME','USERDOMAIN'])if(process.env[k])cleanEnv[k]=process.env[k];Object.assign(cleanEnv,{HOME:home,USERPROFILE:home,AIFY_SERVICE_REGISTRY:registry});
async function child(overrides,code){return await new Promise(resolve=>{const p=spawn(process.execPath,['--input-type=module','-e',code],{env:{...cleanEnv,...overrides},stdio:['ignore','pipe','pipe']});let stdout='',stderr='';p.stdout.on('data',d=>stdout+=d);p.stderr.on('data',d=>stderr+=d);p.on('exit',rc=>resolve({rc,stdout,stderr}));});}
const observations=[];
async function invoke(label,registered,env,client='endpoint'){
 setRegistry(registered);
 const code=client==='endpoint'?`const m=await import(${JSON.stringify(base+'aify-service-endpoint.mjs')});await m.httpCall('GET','/review-synthetic');`:`const m=await import(${JSON.stringify(base+'aify-http.mjs')});await m.makeAifyHttpCall(m.AIFY_SERVER_URL,m.AIFY_API_KEY)('GET','/review-synthetic');`;
 const result=await child(env,code);assert.equal(result.rc,0,result.stderr);
 const record={case:label,good:good.requests.splice(0),foreign:foreign.requests.splice(0),broken:broken.requests.splice(0),redirect:redirect.requests.splice(0)};
 observations.push(record);console.log(JSON.stringify(record));return record;
}
try{
 assert.equal((await invoke('matching-positive',good.url,{AIFY_SERVER_URL:good.url})).good[0].keyMatches,true);
 assert.equal((await invoke('primary-mismatch',good.url,{AIFY_SERVER_URL:foreign.url})).foreign[0].hasKey,false);
 assert.equal((await invoke('claude-precedence-mismatch',good.url,{AIFY_SERVER_URL:good.url,CLAUDE_MCP_SERVER_URL:foreign.url})).foreign[0].hasKey,false);
 assert.equal((await invoke('store-failover',broken.url,{AIFY_SERVER_URL:broken.url,AIFY_SERVER_FALLBACK_URLS:foreign.url})).foreign[0].hasKey,false);
 assert.equal((await invoke('env-failover-control',broken.url,{AIFY_SERVER_URL:broken.url,AIFY_SERVER_FALLBACK_URLS:foreign.url,AIFY_API_KEY:fake})).foreign[0].keyMatches,true);
 assert.equal((await invoke('endpoint-client-redirect',redirect.url,{AIFY_SERVER_URL:redirect.url})).foreign[0].keyMatches,true);
 assert.equal((await invoke('standalone-client-redirect',redirect.url,{AIFY_SERVER_URL:redirect.url},'standalone')).foreign[0].keyMatches,true);
 setRegistry(good.url);
 const samples=[];
 for(let i=0;i<12;i++){for(const mode of (i%2?['store','env']:['env','store'])){const begin=performance.now();const response=await child({AIFY_SERVER_URL:good.url,...(mode==='env'?{AIFY_API_KEY:fake}:{})},`const t=performance.now();const m=await import(${JSON.stringify(base+'aify-service-endpoint.mjs')});if(!m.API_KEY)throw Error('no synthetic key');console.log(performance.now()-t);`);assert.equal(response.rc,0,response.stderr);samples.push({pair:i,mode,wallMs:performance.now()-begin,importMs:Number(response.stdout)});}}
 fs.writeFileSync(rootEvidence+'/b28-cold-load.json',JSON.stringify(samples,null,2));
 console.log(JSON.stringify({timingSamples:samples.length,timingScope:'fresh child endpoint-module import only; filesystem cache not cleared; not full MCP discovery',home}));
}finally{await Promise.all(servers.map(s=>new Promise(r=>s.close(r))));}
fs.writeFileSync(rootEvidence+'/b28-security-results.json',JSON.stringify(observations,null,2));
