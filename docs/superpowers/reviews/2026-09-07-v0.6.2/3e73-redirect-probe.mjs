import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import http from 'node:http';import {spawn} from 'node:child_process';import {pathToFileURL} from 'node:url';
const root='C:/Users/Administrator/AppData/Local/Temp/aify-v062-review';
const base=pathToFileURL(root+'/3e73-source/mcp/stdio/').href;
const leakClient=process.argv[2]||'none';
const home=fs.mkdtempSync(path.join(os.tmpdir(),'3e73-synthetic-'));
const store=path.join(home,'.aify','credentials'),ref='review.key',fake='SYNTHETIC-REVIEW-ONLY';
const {writeCredentialFile}=await import(pathToFileURL(root+'/env-55b-source/lib/credential-fs.mjs'));
assert.equal((await writeCredentialFile({root:store,ref,value:fake})).ok,true);
let status=200,primaryRequests=[],foreignRequests=[];
const foreign=http.createServer((q,r)=>{foreignRequests.push({hasKey:q.headers['x-api-key']===fake});r.writeHead(200,{'Content-Type':'application/json'});r.end('{}');});
await new Promise(r=>foreign.listen(0,'127.0.0.1',r));
const primary=http.createServer((q,r)=>{primaryRequests.push({hasKey:q.headers['x-api-key']===fake});r.writeHead(status,{'Content-Type':'application/json',Location:`http://127.0.0.1:${foreign.address().port}/unrelated`});r.end('{}');});
await new Promise(r=>primary.listen(0,'127.0.0.1',r));
const url=`http://127.0.0.1:${primary.address().port}`;
fs.writeFileSync(path.join(home,'.aify','services.json'),JSON.stringify({services:{'aify-comms':{endpoint:url,credentialRef:ref}}}));
const env={};for(const k of ['PATH','SystemRoot','SYSTEMROOT','WINDIR','TEMP','TMP','USERNAME','USERDOMAIN'])if(process.env[k])env[k]=process.env[k];Object.assign(env,{HOME:home,USERPROFILE:home,AIFY_SERVER_URL:url});
async function request(client){const code=client==='endpoint'?`const m=await import(${JSON.stringify(base+'aify-service-endpoint.mjs')});await m.httpCall('GET','/review');`:`const m=await import(${JSON.stringify(base+'aify-http.mjs')});await m.makeAifyHttpCall(m.AIFY_SERVER_URL,m.AIFY_API_KEY)('GET','/review');`;return await new Promise(resolve=>{const p=spawn(process.execPath,['--input-type=module','-e',code],{env,stdio:['ignore','pipe','pipe']});let err='';p.stderr.on('data',d=>err+=d);p.on('exit',rc=>resolve({rc,err}));});}
try{
 for(const client of ['endpoint','standalone'])for(status of [200,301,302,303,307,308]){
  primaryRequests=[];foreignRequests=[];const result=await request(client);
  const shouldLeak=client===leakClient&&status!==200;
  assert.equal(primaryRequests.length,1);assert.equal(primaryRequests[0].hasKey,true,'positive credential precondition');
  if(status===200){assert.equal(result.rc,0,result.err);assert.deepEqual(foreignRequests,[]);}
  else if(shouldLeak){assert.equal(result.rc,0,result.err);assert.deepEqual(foreignRequests,[{hasKey:true}]);}
  else{assert.notEqual(result.rc,0);assert(result.err.includes('HTTP '+status),result.err);assert.deepEqual(foreignRequests,[]);}
  console.log(JSON.stringify({client,status,exit:result.rc,primaryKey:true,foreignRequests:foreignRequests.length,foreignKey:foreignRequests.some(r=>r.hasKey),expectedLeak:shouldLeak}));
 }
}finally{await Promise.all([primary,foreign].map(s=>new Promise(r=>s.close(r))));fs.rmSync(home,{recursive:true,force:true});}
