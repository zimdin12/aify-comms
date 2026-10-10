import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { buildReport } from "../doctor-report.mjs";
import { markFor } from "../doctor-mark.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const moduleUrl = new URL("../agent-state-shadow-check.mjs", import.meta.url);
const load = async () => {
  assert.ok(existsSync(moduleUrl), "missing production agent-state-shadow-check.mjs");
  return import(moduleUrl);
};
const purposes = ["status word", "busy", "send-time queue", "claim", "worker readiness", "live counts", "reminder skip (B21)", "stranded-reply guard (B26)", "running-run promotion", "stale-turn ceiling"];
const gaps = ["turn.ageMs", "runtime provenance", "roster gates", "running-run promotion", "other nine C8 owners"];
const counts = (disagreed = 0) => ({ observed: 5, nonEvaluation: 1, evaluated: 4, partialCompared: 3, partialAgreed: 3 - disagreed, partialDisagreed: disagreed, unavailable: 1 });
const zero = () => Object.fromEntries(Object.keys(counts()).map(k => [k, 0]));
function fixture(disagreed = 0) {
  const byPurpose = Object.fromEntries(purposes.map(name => [name, { instrumented: false, comparisonScope: "not-instrumented", counts: null, bySource: null, neverObservedClasses: null }]));
  byPurpose["status word"] = { instrumented: true, comparisonScope: "partial-freshness-base-word", counts: counts(disagreed), bySource: { refresh: counts(disagreed), "cache-broadcast": zero(), "engine-status": zero() }, neverObservedClasses: [], unavailableByReason: {} };
  return { schemaVersion: 1, scope: "partial-freshness-base-word", window: "process", coverageComplete: false, qualification: "UNAVAILABLE", fullQualifiedComparisons: 0, versions: { env: null, wrapper: null, comms: null }, qualificationGaps: [...gaps], byPurpose };
}

test("partial agreement retains bounded evidence but never qualifies or passes", async () => {
  const { shadowReportVerdict } = await load();
  const input = fixture();
  input.lastObservation = { agentId: "PRIVATE", notes: "SECRET" };
  input.qualificationGaps = [...gaps, "SECRET"];
  const before = structuredClone(input);
  const verdict = shadowReportVerdict(input);
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "skipped");
  assert.deepEqual(verdict.shadow.byPurpose["status word"].counts, counts());
  assert.equal(verdict.shadow.byPurpose.busy.counts, null);
  assert.deepEqual(verdict.shadow.versions, input.versions);
  assert.deepEqual(verdict.shadow.qualificationGaps, gaps);
  assert.doesNotMatch(JSON.stringify(verdict), /PRIVATE|SECRET|lastObservation|unavailableByReason|neverObservedClasses/);
  assert.deepEqual(input, before, "pure verdict changed its input");
  const report = buildReport([{ id: "agent-state-shadow", ...verdict }]);
  assert.equal(report.ok, true, "qualification skip changed top-level ok");
  assert.equal(report.passed, 0);
  assert.equal(report.skipped, 1);
  assert.equal(report.checks[0].ok, false);
  assert.deepEqual(JSON.parse(JSON.stringify(report)).checks[0].shadow, verdict.shadow);
});

test("partial disagreement is a failed row with failure glyph and retained counts", async () => {
  const { shadowReportVerdict } = await load();
  const verdict = shadowReportVerdict(fixture(2));
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "partial");
  assert.equal(verdict.shadow.byPurpose["status word"].counts.partialDisagreed, 2);
  assert.match(verdict.detail, /2.*disagree/);
  assert.equal(markFor(verdict), "✗");
  assert.equal(buildReport([{ id: "agent-state-shadow", ...verdict }]).failed, 1);
});

test("unreadable and zero-comparison evidence never passes", async () => {
  const { shadowReportVerdict, checkAgentStateShadow } = await load();
  const empty = fixture();
  empty.byPurpose["status word"].counts = zero();
  empty.byPurpose["status word"].bySource.refresh = zero();
  for (const input of [null, undefined, empty]) {
    const verdict = shadowReportVerdict(input);
    assert.equal(verdict.ok, false);
    assert.equal(verdict.code, "skipped");
  }
  const rows = [];
  await checkAgentStateShadow({ get: async () => { throw new Error("SECRET"); }, add: () => assert.fail("unreadable report added a result"), skip: (...args) => rows.push(args) });
  assert.equal(rows.length, 1);
  assert.doesNotMatch(JSON.stringify(rows), /SECRET/);
});

test("malformed schema, scope, counts and unknown owners refuse all evidence", async () => {
  const { shadowReportVerdict } = await load();
  const edits = [
    r => r.schemaVersion = 2, r => r.scope = "full", r => r.window = "fleet",
    r => r.coverageComplete = true, r => r.qualification = "QUALIFIED", r => r.fullQualifiedComparisons = 1,
    r => r.versions.env = "HEAD", r => delete r.versions.wrapper, r => r.qualificationGaps = [],
    r => delete r.byPurpose.busy, r => r.byPurpose.extra = r.byPurpose.busy,
    r => r.byPurpose.busy.counts = zero(), r => r.byPurpose.busy.instrumented = true,
    r => r.byPurpose["status word"].comparisonScope = "full",
    r => r.byPurpose["status word"].counts.observed++,
    r => r.byPurpose["status word"].counts.evaluated++,
    r => r.byPurpose["status word"].counts.partialAgreed++,
    r => r.byPurpose["status word"].counts.unavailable = -1,
    r => r.byPurpose["status word"].counts.partialCompared = 1.5,
    r => r.byPurpose["status word"].counts.observed = Number.MAX_SAFE_INTEGER + 1,
    r => r.byPurpose["status word"].counts.observed = "5",
    r => delete r.byPurpose["status word"].counts.unavailable,
    r => delete r.byPurpose["status word"].bySource.refresh,
    r => r.byPurpose["status word"].bySource.refresh = zero(),
    r => r.byPurpose["status word"].bySource["engine-status"].observed = -1,
  ];
  for (const edit of edits) {
    const input = fixture(); edit(input);
    const verdict = shadowReportVerdict(input);
    assert.equal(verdict.ok, false, String(edit));
    assert.equal(verdict.code, "skipped", String(edit));
    assert.equal(verdict.shadow, undefined, "malformed evidence was retained");
  }
});

test("declared classes and reasons reject unknown labels and invalid counts", async () => {
  const { shadowReportVerdict } = await load();
  for (const edit of [
    r => r.qualificationGaps.push(null),
    r => r.byPurpose["status word"].neverObservedClasses = ["PRIVATE-ID"],
    r => r.byPurpose["status word"].neverObservedClasses = ["partial-agree", "partial-agree"],
    r => r.byPurpose["status word"].unavailableByReason = { "raw-error": 1 },
    r => r.byPurpose["status word"].unavailableByReason = { "unknown-loss": -1 },
  ]) {
    const input = fixture(); edit(input);
    const verdict = shadowReportVerdict(input);
    assert.equal(verdict.code, "skipped");
    assert.equal(verdict.shadow, undefined, String(edit));
  }
  const input = fixture();
  input.byPurpose["status word"].neverObservedClasses = ["unknown-loss"];
  input.byPurpose["status word"].unavailableByReason = { "unknown-loss": 1 };
  assert.ok(shadowReportVerdict(input).shadow, "valid fixed catalog labels refused");
});

// Execute the actual source-bound doctor trigger, transport and row collectors.
// This does not import the whole doctor or touch a service, host process or credentials.
async function doctorTrigger(body) {
  const source = readFileSync(new URL("../doctor.js", import.meta.url), "utf8");
  const invocation = source.match(/^await checkAgentStateShadow\(\{ get, add, skip \}\);$/gm) || [];
  assert.equal(invocation.length, 1, "doctor must execute exactly one production shadow trigger");
  assert.match(source, /import \{ checkAgentStateShadow \} from "\.\/agent-state-shadow-check\.mjs";/);
  const collectors = source.slice(source.indexOf("const checks = [];"), source.indexOf("const sh ="));
  const transport = source.slice(source.indexOf("let serviceRefusedTheKey = false;"), source.indexOf("/** Why the service produced nothing"));
  const { checkAgentStateShadow } = await load();
  const requests = [];
  const fetch = async (url, options) => {
    requests.push({ url, options });
    assert.equal(url, "http://fixture.invalid/api/v1/agent-state/shadow-report");
    assert.equal(options.method ?? "GET", "GET");
    assert.deepEqual(options.headers, { "X-API-Key": "synthetic-test-key" });
    assert.equal(options.redirect, "manual");
    return { ok: true, status: 200, json: async () => body };
  };
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const run = new AsyncFunction("checkAgentStateShadow", "fetch", "SERVER_URL", "DOCTOR_API_KEY", `${collectors}\n${transport}\n${invocation[0]}\nreturn checks;`);
  const rows = await run(checkAgentStateShadow, fetch, "http://fixture.invalid", { key: "synthetic-test-key" });
  assert.equal(requests.length, 1, "shadow report was not read exactly once");
  assert.equal(rows.length, 1);
  return JSON.parse(JSON.stringify(buildReport(rows)));
}

test("existing unrelated doctor skips preserve their original row bytes", () => {
  const source = readFileSync(new URL("../doctor.js", import.meta.url), "utf8");
  const collectors = source.slice(source.indexOf("const checks = [];"), source.indexOf("const sh ="));
  const rows = new Function(`${collectors}\nskip("unrelated", "not applicable"); return checks;`)();
  assert.deepEqual(rows, [{ id: "unrelated", ok: true, code: "skipped", detail: "not applicable" }]);
  assert.deepEqual(buildReport(rows).checks, [{ ...rows[0], ok: false, skipped: true }]);
});

test("actual doctor trigger keeps unreadable shadow skips false without a carrier", async () => {
  const report = await doctorTrigger(null);
  assert.equal(report.checks[0].ok, false);
  assert.equal(report.checks[0].code, "skipped");
  assert.equal(report.checks[0].shadow, undefined);
  assert.equal(report.ok, true);
});

test("actual doctor trigger uses authenticated get and skip carrier reaches JSON", async () => {
  const report = await doctorTrigger(fixture());
  assert.equal(report.ok, true);
  assert.equal(report.skipped, 1);
  assert.equal(report.checks[0].id, "agent-state-shadow");
  assert.equal(report.checks[0].ok, false);
  assert.deepEqual(report.checks[0].shadow.byPurpose["status word"].counts, counts());
});

test("joined isolated Python GET responses survive the real doctor trigger and JSON normalization", async () => {
  const root = process.env.AIFY_COMMS_REPO || fileURLToPath(new URL("../../../", import.meta.url));
  assert.ok(existsSync(`${root}/service/api_core/partial_status_shadow.py`), "explicit Python checkout lacks shadow implementation");
  const program = String.raw`
import asyncio, hashlib, json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from service import db
from service.api_core import partial_status_shadow as shadow
from service.api_core.agent_state_shadow import AgentStateShadowStore
from service.routers.agent_state import router
from service.status_engine import StatusInputs
assert 'service.main' not in sys.modules, 'live main import forbidden'
root = Path.cwd()
files = ['service/api_core/partial_status_shadow.py', 'service/api_core/agent_state_shadow.py', 'service/routers/agent_state.py']
source = {p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files}
with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as private:
    asyncio.run(db.init_db(Path(private)/'fixture.db'))
    shadow.mirror, shadow.recorder = shadow.Mirror(), shadow.Recorder()
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    reports = []
    with TestClient(app) as client:
        def capture():
            before = shadow.recorder.report()
            response = client.get('/api/v1/agent-state/shadow-report')
            assert response.status_code == 200, response.text
            assert response.json() == before == shadow.recorder.report()
            reports.append(response.text)
        capture()
        at = '2026-10-07T09:00:00Z'
        row = dict(agentId='private-agent', lifetime='private-lifetime', state='working', stateCause='turn-open', busy=True,
                   process=dict(state='running', verified='yes', pid=4242),
                   turn=dict(open=True, startedAtUs=1700000000000000, lastEventAtUs=1700000000000000,
                             awaitingInput=False, busyIf=dict(strict=True, verifiedRenewal=True)))
        publication = dict(kind='snapshot', machineId='private-host', instance='private-publisher', generation=10,
                           incarnationId='boot', publication=1, inputs={'operatorStop':'not-tracked'},
                           agents=[row], removed=[], complete=True)
        assert asyncio.run(AgentStateShadowStore().apply(json.dumps(publication).encode(), at)).applied
        inputs = StatusInputs(mode='managed', alive=True, in_turn=True, awaiting_input=False, worker_present=True,
                              env_reachable=True, disabled=False, bridge_stale=False, has_live_session=True)
        with patch.object(shadow, 'clock', return_value=at):
            shadow.observe(shadow.bind(), {'id':'private-agent','machine_id':'private-host'}, inputs, 'working', 'refresh')
            capture()
            shadow.observe(shadow.bind(), {'id':'private-agent','machine_id':'private-host'}, inputs, 'available', 'engine-status')
            capture()
    assert 'service.main' not in sys.modules
print(json.dumps({'reports':reports, 'source':source, 'python':sys.executable}))
`;
  const result = spawnSync(process.env.AIFY_SHADOW_PYTHON || "python", ["-X", "utf8", "-B", "-c", program], {
    cwd: root, encoding: "utf8", timeout: 30000, env: sealedChildEnv({ PYTHONDONTWRITEBYTECODE: "1" }),
  });
  assert.equal(result.status, 0, `${result.error || ""}\n${result.stderr}\n${result.stdout}`);
  const captured = JSON.parse(result.stdout);
  console.log(`joined-python-capture ${JSON.stringify(captured)}`);
  const { shadowReportVerdict } = await load();
  for (const [index, text] of captured.reports.entries()) {
    const body = JSON.parse(text);
    const verdict = shadowReportVerdict(body);
    assert.ok(verdict.shadow, "actual Python response rejected");
    assert.equal(verdict.ok, false);
    const report = await doctorTrigger(body);
    assert.deepEqual(report.checks[0].shadow, verdict.shadow);
    assert.equal(report.checks[0].ok, false);
    assert.equal(report.checks[0].code, index === 2 ? "partial" : "skipped");
    assert.equal(report.ok, index !== 2);
    assert.equal(report.checks[0].shadow.byPurpose["status word"].counts.partialCompared, index);
    assert.doesNotMatch(JSON.stringify(report), /private-agent|private-host|private-publisher|private-lifetime|lastObservation/);
  }
});

test("actual doctor trigger consumes failed verdict and add carrier reaches JSON", async () => {
  const report = await doctorTrigger(fixture(1));
  assert.equal(report.ok, false);
  assert.equal(report.failed, 1);
  assert.equal(report.checks[0].code, "partial");
  assert.equal(report.checks[0].shadow.byPurpose["status word"].counts.partialDisagreed, 1);
});
