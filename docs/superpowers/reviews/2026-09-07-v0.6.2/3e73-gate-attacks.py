import pathlib,subprocess,json,hashlib
root=pathlib.Path('C:/Users/Administrator/AppData/Local/Temp/aify-v062-review');source=root/'3e73-source'
ep=source/'mcp/stdio/aify-service-endpoint.mjs';http=source/'mcp/stdio/aify-http.mjs'
original={ep:ep.read_bytes(),http:http.read_bytes()}
gate=['node','--test','mcp/stdio/tests/a-request-carrying-the-key-never-follows-a-redirect.test.js']
results=[]
def run(label,args,expected):
 p=subprocess.run(args,cwd=source,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 (root/('3e73-'+label+'.log')).write_bytes(p.stdout)
 result={'label':label,'exit':p.returncode,'sha256':hashlib.sha256(p.stdout).hexdigest()};results.append(result);print(json.dumps(result));assert p.returncode==expected,p.stdout.decode('utf8','replace')
 return p.stdout.decode('utf8','replace')
try:
 run('baseline-gate',gate,0)
 run('baseline-runtime',['node',str(root/'3e73-redirect-probe.mjs')],0)
 token=b'const res = await fetch(url, { ...options, redirect: "manual" });'
 assert original[ep].count(token)==1
 arms=[
 ('literal-delete',ep,original[ep].replace(token,b'const res = await fetch(url, { ...options });'),1,'endpoint'),
 ('comment-only',ep,original[ep].replace(token,b'const res = await fetch(url, { ...options /* redirect: "manual" */ });'),0,'endpoint'),
 ('spread-follow',ep,original[ep].replace(token,b'const res = await fetch(url, { ...options, redirect: "manual", ...{ redirect: "follow" } });'),0,'endpoint'),
 ('lowercase-header',http,original[http].replace(b'X-API-Key',b'x-api-key').replace(b'...options, redirect: "manual", signal:',b'...options, signal:'),0,'standalone')]
 for label,file,data,rc,client in arms:
  for f,buf in original.items():f.write_bytes(buf)
  file.write_bytes(data)
  result=run(label+'-gate',gate,rc)
  if rc:assert 'aify-service-endpoint.mjs' in result
  run(label+'-runtime',['node',str(root/'3e73-redirect-probe.mjs'),client],0)
finally:
 for f,buf in original.items():f.write_bytes(buf);assert f.read_bytes()==buf
(root/'3e73-mutation-results.json').write_text(json.dumps(results,indent=2),encoding='utf8')
run('restored-gate',gate,0)
