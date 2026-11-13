import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

/** The real environment tier stays inside this run's one host container and persistent home. */
export class StagingHost {
  constructor(session, stack) {
    this.session = session;
    this.stack = stack;
    this.name = `${session.layout.project}-host`;
  }

  install(phase) {
    const s = this.session;
    const script = fileURLToPath(new URL('./fixtures/bootstrap-host.sh', import.meta.url));
    s.requireCommand(['cp', script, `${this.name}:/work/bootstrap-host.sh`], 'copy staging bootstrap');
    const output = s.requireCommand(['exec', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`, this.name,
      'bash', '/work/bootstrap-host.sh', `/src/${phase}`], 'owner installers');
    s.save(`${phase}-install.txt`, output);
    for (const [role, ref] of Object.entries(s.manifest[phase])) {
      s.check(`${phase}_installed_source_${role}`, output.includes(`source ${role} ${ref}`), 'owner script read the exact staged source HEAD');
    }
  }

  installRuntime() {
    const s = this.session;
    s.requireCommand(['exec', this.name, 'mkdir', '-p', '/work/e4-runtime'], 'staging runtime directory');
    for (const file of ['codex-runtime.mjs', 'turn-brain.mjs']) {
      s.requireCommand(['cp', fileURLToPath(new URL(`./fixtures/${file}`, import.meta.url)), `${this.name}:/work/e4-runtime/${file}`], 'copy runtime fixture');
    }
    const script = `const fs=require('fs');const p='/work/e4-runtime/node_modules';
      if(!fs.existsSync(p))fs.symlinkSync('/home/staging/.aify-comms/mcp/stdio/node_modules',p,'dir');
      if(fs.realpathSync(p)!=='/home/staging/.aify-comms/mcp/stdio/node_modules')throw new Error('foreign runtime dependencies');
      fs.writeFileSync('/home/staging/.local/bin/codex','#!/usr/bin/env bash\\nexec node /work/e4-runtime/codex-runtime.mjs "$@"\\n',{mode:0o755});`;
    s.requireCommand(['exec', this.name, 'node', '-e', script], 'fixture runtime on the real launcher PATH');
  }

  defineFleet(phase, environment = []) {
    const s = this.session;
    this.ids = ['worker', 'stopped', 'configured'].map(role => `e4-${role}-${s.layout.runId}`);
    for (const id of this.ids) {
      s.requireCommand(['exec', ...environment.flatMap(pair => ['-e', pair]), this.name, 'node', `/src/${phase}/env/bin/aify-env.mjs`, 'agents', 'set', id,
        `name=${id}`, 'role=staging-fixture', 'harness=codex', 'mode=managed', 'workspace=/work', 'herdrSpace=false',
        'env.STAGING_REPLY_TO=dashboard', `env.FIXTURE_TAG=${id}`], 'definition through the real owner CLI');
    }
    this.definitions = this.readDefinitions(phase);
    s.save(`${phase}-definitions.json`, this.definitions);
    return [...this.ids];
  }

  readDefinitions(phase) {
    return this.ids.map(id => JSON.parse(this.session.requireCommand(['exec', this.name, 'node',
      `/src/${phase}/env/bin/aify-env.mjs`, 'agents', 'show', id], 'definition readback through owning CLI')));
  }

  async waitForDefinitions(phase) {
    const s = this.session;
    const deadline = Date.now() + 75_000;
    while (Date.now() < deadline) {
      const records = await Promise.all(this.ids.map(id => this.stack.request(`/api/v1/agents/${id}`, s.layout.fixtureKey)));
      if (records.every(record => record.status === 200)) {
        s.check(`${phase}_definitions_published`, true, { ids: [...this.ids] });
        this.agentRecords = records.map(record => JSON.parse(record.body));
        s.save(`${phase}-agent-records.json`, this.agentRecords);
        return this.agentRecords;
      }
      await delay(500);
    }
    throw new Error('definitions did not reach real staging comms');
  }

  async startWorker(label = 'baseline', index = 0) {
    const s = this.session;
    const id = this.ids[index];
    const requested = await this.stack.request(`/api/v1/agents/${id}/control`, s.layout.fixtureKey,
      { method: 'POST', body: { action: 'start', from_agent: 'dashboard' } });
    s.save(`${label}-start-response.json`, requested);
    if (requested.status < 200 || requested.status >= 300) throw new Error('real defined-agent start was refused');
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      const health = this.localHealth();
      const agent = await this.stack.request(`/api/v1/agents/${id}`, s.layout.fixtureKey);
      if (health.status === 0 && agent.status === 200) {
        const body = JSON.parse(agent.body).agent;
        const processes = JSON.parse(health.out).processes;
        if (processes.length && body?.sessionHandle && body.runtimeConfig?.appServerUrl) {
          const witnesses = this.observeProcesses(processes.map(process => process.pid));
          if (!witnesses.length || !witnesses.every(process => process.state !== 'absent' && process.state !== 'unknown' && !['Z', 'X'].includes(process.state))) {
            throw new Error('managed process presence was not confirmed by the container OS');
          }
          this.workerWitnesses = witnesses;
          s.save(`${label}-worker.json`, { agent: body, processes, witnesses });
          s.check(`${label}_managed_worker`, true, { id, sessionHandle: body.sessionHandle, processes, witnesses });
          this.worker = body;
          this.ownedProcesses = processes;
          return body;
        }
      }
      await delay(500);
    }
    this.captureRuntime();
    throw new Error('defined managed runtime never acquired a live native handle');
  }

  observeProcesses(pids) {
    if (!pids.length || pids.some(pid => !Number.isSafeInteger(pid) || pid <= 1)) throw new Error('explicit managed PIDs are required');
    const script = `const fs=require('fs');const pids=JSON.parse(process.argv[1]);console.log(JSON.stringify(pids.map(pid=>{
      try{const text=fs.readFileSync('/proc/'+pid+'/stat','utf8');const fields=text.slice(text.lastIndexOf(')')+2).trim().split(/\\s+/);
        return {pid,state:fields[0],startTicks:fields[19]};}
      catch(error){return {pid,state:error.code==='ENOENT'?'absent':'unknown',code:error.code};}})));`;
    return JSON.parse(this.session.requireCommand(['exec', this.name, 'node', '-e', script, JSON.stringify([...pids])], 'OS process identity observation'));
  }

  async probeWorkloop(label = 'baseline') {
    const s = this.session;
    const nonce = `${s.layout.runId}${Date.now().toString(16)}`;
    const sent = await this.stack.request('/api/v1/messages/send', s.layout.fixtureKey, { method: 'POST', body: {
      from_agent: 'dashboard', to: this.ids[0], type: 'request', subject: 'Staging lifecycle probe',
      body: `E4-PROBE-${nonce}`, trigger: true, requireReply: true,
    } });
    if (sent.status !== 200) throw new Error('staging work request was refused');
    const acknowledged = JSON.parse(sent.body);
    s.save(`${label}-send.json`, acknowledged);
    const runId = acknowledged.dispatchRuns?.find(run => run.targetAgentId === this.ids[0])?.runId;
    if (!acknowledged.messageId || !runId) throw new Error('request created no exact worker run');
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      const observed = await this.stack.request(`/api/v1/dispatch/runs/${runId}`, s.layout.fixtureKey);
      const run = observed.status === 200 ? JSON.parse(observed.body).run : null;
      if (run?.resultMessageId) {
        const response = await this.stack.request(`/api/v1/messages/inbox/dashboard?filter=all&peek=true&messageId=${encodeURIComponent(run.resultMessageId)}`, s.layout.fixtureKey);
        const data = JSON.parse(response.body);
        const message = data.messages?.find(item => item.id === run.resultMessageId);
        s.save(`${label}-linked-reply.json`, { run, response: data });
        s.check(`${label}_linked_reply`, response.status === 200 && message?.inReplyTo === acknowledged.messageId
          && message.body === `E4-REPLY-${nonce}` && message.from === this.ids[0], { requestId: acknowledged.messageId, runId, resultMessageId: run.resultMessageId });
        return { requestId: acknowledged.messageId, runId, replyId: run.resultMessageId };
      }
      await delay(500);
    }
    this.captureRuntime();
    throw new Error('real dispatch never produced the exact run-linked reply');
  }

  captureRuntime() {
    const s = this.session;
    const program = `const fs=require('fs');for(const name of ['runtime.jsonl','turns.jsonl']){
      const p='/work/e4-runtime/'+name;if(fs.existsSync(p))console.log(JSON.stringify({name,content:fs.readFileSync(p,'utf8')}));}`;
    s.command(['exec', this.name, 'node', '-e', program]);
    s.command(['exec', this.name, 'node', '-e', "const fs=require('fs');const p='/work/baseline-env.log';if(fs.existsSync(p))process.stdout.write(fs.readFileSync(p,'utf8'))"]);
    const packed = s.command(['exec', this.name, 'tar', '--exclude=node_modules', '-czf', '/work/runtime-evidence.tar.gz', '-C', '/work', 'e4-runtime']);
    if (packed.status !== 0) return false;
    return s.command(['cp', `${this.name}:/work/runtime-evidence.tar.gz`, `${s.out}/runtime-evidence.tar.gz`]).status === 0;
  }

  localHealth() {
    return this.session.command(['exec', this.name, 'node', '--input-type=module', '-e',
      "const r=await fetch('http://127.0.0.1:8802/health',{signal:AbortSignal.timeout(2000)});if(!r.ok)throw new Error('env health '+r.status);console.log(await r.text());"]);
  }

  async startBaseline(phase = 'baseline', environment = []) {
    const s = this.session;
    const before = this.localHealth();
    s.check(`${phase}_env_initially_absent`, before.status === 1 && /ECONNREFUSED/.test(before.out),
      'no local env answered in this fresh container before the real start');
    s.requireCommand(['exec', '-d', '-w', '/work', '-e', 'AIFY_NO_DASHBOARD=1', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`,
      ...environment.flatMap(pair => ['-e', pair]), this.name, 'bash', '-c',
      `exec node /src/${phase}/env/bin/aify-env.mjs --port 8802 > /work/baseline-env.log 2>&1`],
    'selected environment start inside staging container');
    const deadline = Date.now() + 45_000;
    while (Date.now() < deadline) {
      const result = this.localHealth();
      if (result.status === 0) {
        const health = JSON.parse(result.out);
        if (health.status !== 'healthy' || !Number.isSafeInteger(health.pid) || health.pid <= 1
            || !health.build || health.build !== health.codeOnDisk) throw new Error('baseline environment identity is not established');
        const process = s.requireCommand(['exec', this.name, 'ps', '-p', String(health.pid), '-o', 'pid=,stat=,args='], 'baseline environment OS witness');
        s.check(`${phase}_env_linux`, process.includes(`/src/${phase}/env/bin/aify-env.mjs`) && !/^\s*\d+\s+[ZX]/.test(process),
          { health, process });
        s.save('baseline-env-health.json', health);
        const environments = await this.stack.request('/api/v1/environments', s.layout.fixtureKey);
        s.save('baseline-service-environments.json', environments);
        return health;
      }
      await delay(250);
    }
    s.command(['exec', this.name, 'node', '-e', "process.stdout.write(require('fs').readFileSync('/work/baseline-env.log','utf8'))"]);
    throw new Error('unchanged baseline env did not become healthy on Linux; inspect retained baseline-env log before any fallback');
  }
}
