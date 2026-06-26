# Usage / Quota Tracking + Stats Screen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface per-pool subscription quota remaining (Anthropic Claude Max, OpenAI ChatGPT-Codex) plus per-agent token consumption, so agents/operator see when a pool nears exhaustion — advisory only — with a revamped stats screen.

**Architecture:** A collector in the host env-bridge polls each quota pool (~3 min) and POSTs to the service; the service holds an in-memory `_USAGE_CACHE` (single-worker) and a consumption summarizer; a `comms_usage` tool, `comms_agent_info` fields, and the dashboard stats screen read that cache. Old dashboard (8800) first, new dashboard (8801) last.

**Tech Stack:** Node ESM (mcp/stdio bridge), Python FastAPI (service/routers/api_v2.py), vanilla JS (service/dashboard.html), ES-module SPA (service/new_dashboard/).

## Global Constraints

- **In-memory cache ONLY** — `_USAGE_CACHE` is a process-global dict like `_LIVE_STATE_CACHE`; the service MUST stay single-worker uvicorn. Never add a SQLite table for live usage.
- **Spec is authoritative:** `docs/superpowers/specs/2026-06-26-usage-quota-stats-design.md`.
- **Advisory only:** never let usage gate `comms_send` or change `derive()`.
- **Bridge changes** (`mcp/stdio/`) need `install.sh` re-run + wrapper restart to deploy; **service changes** need a container rebuild. Don't deploy mid-plan; build + commit.
- **No opencode tests.** Node: `node --check <file>` + `node tests/<file>.js`. Python: `python -m pytest <file> -q`.
- **Source ids (verbatim):** `anthropic-claude-max`, `openai-chatgpt-codex`, `local-ollama`.
- **Anthropic usage:** `GET https://api.anthropic.com/api/oauth/usage`, headers `authorization: Bearer <tok>` + `anthropic-beta: oauth-2025-04-20`; token at `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`. Fields: `five_hour.utilization`, `seven_day.utilization`, `limits[].{percent,severity,resets_at,is_active}`. `utilization`/`percent` = % USED.
- **Codex usage:** latest `~/.codex/sessions/**/rollout-*.jsonl` → `rate_limits.{primary:{used_percent,resets_at},secondary:{used_percent,resets_at},plan_type}` (primary=5h, secondary=weekly).
- **Thresholds (settings):** `usage_warn_pct` default 90, `usage_critical_pct` default 98.
- **`pctLeft(used) = 100 - used`.**

## File Structure

| File | Responsibility |
|------|----------------|
| `mcp/stdio/usage-collector.js` (new) | Pure adapters + helpers: `fetchAnthropicUsage`, `fetchCodexUsage`, `pctLeft`, `severityFor`, `normalizeUsage`. Injected fetch/fs for tests. |
| `mcp/stdio/tests/usage-collector.test.js` (new) | Unit tests for the above. |
| `mcp/stdio/server.js` (modify) | Start the collector loop under `IS_ENVIRONMENT_BRIDGE`; add `comms_usage` tool. |
| `service/usage_cache.py` (new) | `_USAGE_CACHE` dict + `usage_set/get/all`, `severity_for`, `consumption` summarizer. Pure, importable, unit-testable. |
| `service/tests/test_usage_cache.py` (new) | Unit tests for usage_cache. |
| `service/routers/api_v2.py` (modify) | `POST/GET /usage`; `usageSource` auto-bind on register + `PATCH /agents/{id}/usage-source`; usage fields in agent detail. |
| `service/tests/test_usage_api.py` (new) | API round-trip + auto-bind + override tests. |
| `service/dashboard.html` (modify) | Analytics page: Pools band + Consumption section (PRIMARY UI). |
| `service/tests/test_dashboard_usage.py` (new) | String-match tests for the old-dashboard UI wiring. |
| `service/new_dashboard/` (modify) | Port Pools band + Consumption (FINAL phase). |

---

## Task 1: Collector pure helpers — `pctLeft`, `severityFor`, `normalizeUsage`

**Files:**
- Create: `mcp/stdio/usage-collector.js`
- Test: `mcp/stdio/tests/usage-collector.test.js`

**Interfaces:**
- Produces: `pctLeft(usedPct:number)->number`; `severityFor(usedPct:number, providerSeverity?:string, warn=90, critical=98)->"normal"|"warning"|"critical"`; `normalizeUsage({sourceId, fiveHourUsed, weeklyUsed, fiveHourResetsAt, weeklyResetsAt, providerSeverity, planType})->{source_id, five_hour:{used_pct,left_pct,resets_at}, weekly:{used_pct,left_pct,resets_at}, severity, plan_type}`.

- [ ] **Step 1: Write the failing test**
```js
// mcp/stdio/tests/usage-collector.test.js
import assert from "node:assert/strict";
import { pctLeft, severityFor, normalizeUsage } from "../usage-collector.js";

assert.equal(pctLeft(81), 19, "pctLeft(81)=19");
assert.equal(pctLeft(0), 100); assert.equal(pctLeft(100), 0);
assert.equal(severityFor(10), "normal", "10% used -> normal");
assert.equal(severityFor(92), "warning", "92% -> warning (>=90)");
assert.equal(severityFor(99), "critical", "99% -> critical (>=98)");
assert.equal(severityFor(50, "warning"), "warning", "provider severity escalates");
assert.equal(severityFor(99, "warning"), "critical", "threshold beats lower provider severity");
const n = normalizeUsage({ sourceId: "anthropic-claude-max", fiveHourUsed: 10, weeklyUsed: 81, fiveHourResetsAt: "2026-06-26T16:00:00Z", weeklyResetsAt: "2026-06-26T17:00:00Z", providerSeverity: "warning", planType: "max" });
assert.equal(n.source_id, "anthropic-claude-max");
assert.equal(n.weekly.left_pct, 19);
assert.equal(n.weekly.used_pct, 81);
assert.equal(n.severity, "warning");
console.log("usage-collector.test.js: helpers ok");
```

- [ ] **Step 2: Run — expect FAIL** `node mcp/stdio/tests/usage-collector.test.js` → "Cannot find module"/not a function.

- [ ] **Step 3: Implement**
```js
// mcp/stdio/usage-collector.js  (helpers section)
export function pctLeft(usedPct) {
  const u = Number(usedPct);
  if (!Number.isFinite(u)) return null;
  return Math.max(0, Math.min(100, 100 - u));
}
const RANK = { normal: 0, warning: 1, critical: 2 };
export function severityFor(usedPct, providerSeverity, warn = 90, critical = 98) {
  let s = "normal";
  const u = Number(usedPct);
  if (Number.isFinite(u)) { if (u >= critical) s = "critical"; else if (u >= warn) s = "warning"; }
  const p = providerSeverity && RANK[providerSeverity] !== undefined ? providerSeverity : "normal";
  return RANK[p] > RANK[s] ? p : s;
}
export function normalizeUsage({ sourceId, fiveHourUsed, weeklyUsed, fiveHourResetsAt, weeklyResetsAt, providerSeverity, planType, warn, critical } = {}) {
  const win = (used, resets) => ({ used_pct: Number(used) ?? null, left_pct: pctLeft(used), resets_at: resets || null });
  return {
    source_id: sourceId,
    five_hour: win(fiveHourUsed, fiveHourResetsAt),
    weekly: win(weeklyUsed, weeklyResetsAt),
    severity: severityFor(weeklyUsed, providerSeverity, warn, critical),
    plan_type: planType || null,
  };
}
```

- [ ] **Step 4: Run — expect PASS** `node mcp/stdio/tests/usage-collector.test.js`.
- [ ] **Step 5: `node --check mcp/stdio/usage-collector.js`**
- [ ] **Step 6: Commit** `git add mcp/stdio/usage-collector.js mcp/stdio/tests/usage-collector.test.js && git commit -m "feat(usage): collector pure helpers (pctLeft/severityFor/normalizeUsage)"`

---

## Task 2: Anthropic + Codex usage adapters

**Files:**
- Modify: `mcp/stdio/usage-collector.js`
- Test: `mcp/stdio/tests/usage-collector.test.js`

**Interfaces:**
- Produces: `fetchAnthropicUsage({credsPath, fetchImpl})->Promise<normalized|{source_id,unknown:true}>`; `fetchCodexUsage({sessionsDir, readImpl})->Promise<normalized|{source_id,unknown:true}>`. Both read the real on-disk shapes (Global Constraints) and call `normalizeUsage`. Never throw; return `{source_id, unknown:true}` on any error/missing creds.

- [ ] **Step 1: Failing test (append)**
```js
// anthropic adapter: shape -> normalized
{
  const fakeFetch = async () => ({ ok: true, status: 200, json: async () => ({
    five_hour: { utilization: 10.0, resets_at: "2026-06-26T16:00:00Z" },
    seven_day: { utilization: 81.0, resets_at: "2026-06-26T17:00:00Z" },
    limits: [{ kind: "weekly_all", group: "weekly", percent: 81, severity: "warning", is_active: true }],
  })});
  const fakeCreds = JSON.stringify({ claudeAiOauth: { accessToken: "x", expiresAt: Date.now() + 1e6 } });
  const { fetchAnthropicUsage } = await import("../usage-collector.js");
  const r = await fetchAnthropicUsage({ readCreds: () => fakeCreds, fetchImpl: fakeFetch });
  assert.equal(r.source_id, "anthropic-claude-max");
  assert.equal(r.weekly.used_pct, 81);
  assert.equal(r.severity, "warning");
}
// codex adapter: rollout rate_limits -> normalized
{
  const rollout = JSON.stringify({ rate_limits: { primary: { used_percent: 1, window_minutes: 300, resets_at: 1778617622 }, secondary: { used_percent: 0, window_minutes: 10080, resets_at: 1779146585 }, plan_type: "prolite" } });
  const { fetchCodexUsage } = await import("../usage-collector.js");
  const r = await fetchCodexUsage({ readLatestRollout: () => rollout });
  assert.equal(r.source_id, "openai-chatgpt-codex");
  assert.equal(r.weekly.used_pct, 0);
  assert.equal(r.five_hour.used_pct, 1);
}
// missing creds -> unknown (never throws)
{
  const { fetchAnthropicUsage } = await import("../usage-collector.js");
  const r = await fetchAnthropicUsage({ readCreds: () => { throw new Error("no creds"); } });
  assert.equal(r.unknown, true); assert.equal(r.source_id, "anthropic-claude-max");
}
console.log("usage-collector.test.js: adapters ok");
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** (append to `usage-collector.js`). Use the constants from Global Constraints. `readCreds`/`readLatestRollout`/`fetchImpl` default to real fs/fetch in production; tests inject. Anthropic: parse creds, extract `claudeAiOauth.accessToken`; GET the usage URL with both headers; map `five_hour.utilization`→fiveHourUsed, `seven_day.utilization`→weeklyUsed, the active `weekly` limit's `severity`→providerSeverity; on `!ok`/throw → `{source_id:"anthropic-claude-max", unknown:true}`. Codex: parse the rollout JSON's last `rate_limits` (scan lines from end for the latest), map `primary.used_percent`/`secondary.used_percent` and `resets_at` (epoch seconds → ISO), `plan_type`; missing → unknown. Both call `normalizeUsage`.
  - *Note:* token-refresh (expired `expiresAt`) is Task 4's concern; here, if expired, still attempt and let a 401 → unknown.

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: `node --check`.**
- [ ] **Step 6: Commit** `-m "feat(usage): anthropic oauth/usage + codex rollout adapters"`

---

## Task 3: Service usage cache + consumption summarizer — `service/usage_cache.py`

**Files:**
- Create: `service/usage_cache.py`
- Test: `service/tests/test_usage_cache.py`

**Interfaces:**
- Produces: module-global `_USAGE_CACHE: dict[str, dict]`; `usage_set(source_id, payload)`, `usage_get(source_id)->dict|None`, `usage_all()->list[dict]` (each with `stale: bool` computed from `updated_at` vs `STALE_AFTER_SECONDS=420`); `summarize_consumption(rows)->dict` where `rows` are `[{agent_id, source_id, model, input_tokens, output_tokens, cache_tokens}]` → `{by_agent, by_model, by_source, totals}`.

- [ ] **Step 1: Failing test**
```python
# service/tests/test_usage_cache.py
from service import usage_cache as uc

def test_set_get_all_roundtrip():
    uc._USAGE_CACHE.clear()
    uc.usage_set("anthropic-claude-max", {"weekly": {"used_pct": 81, "left_pct": 19}, "severity": "warning", "updated_at": "2026-06-26T17:00:00Z"})
    g = uc.usage_get("anthropic-claude-max")
    assert g["weekly"]["left_pct"] == 19
    assert any(p["source_id"] == "anthropic-claude-max" for p in uc.usage_all())

def test_consumption_summary():
    rows = [
        {"agent_id": "a", "source_id": "anthropic-claude-max", "model": "claude-opus-4-8", "input_tokens": 100, "output_tokens": 10, "cache_tokens": 5},
        {"agent_id": "b", "source_id": "openai-chatgpt-codex", "model": "gpt-5.5", "input_tokens": 200, "output_tokens": 20, "cache_tokens": 0},
    ]
    s = uc.summarize_consumption(rows)
    assert s["totals"]["input_tokens"] == 300
    assert s["by_agent"]["a"]["output_tokens"] == 10
    assert s["by_source"]["openai-chatgpt-codex"]["input_tokens"] == 200
```

- [ ] **Step 2: Run — expect FAIL** `python -m pytest service/tests/test_usage_cache.py -q`.

- [ ] **Step 3: Implement** `service/usage_cache.py`: a process-global dict; `usage_set` stamps `source_id` into the payload; `usage_get` returns a shallow copy with `stale` computed (parse `updated_at`, compare to now via `datetime`); `usage_all` maps over keys; `summarize_consumption` folds rows into `by_agent`/`by_model`/`by_source` dicts and a `totals` dict (sum input/output/cache). No DB, no I/O.

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: `python -c "import ast; ast.parse(open('service/usage_cache.py').read())"`.**
- [ ] **Step 6: Commit** `-m "feat(usage): in-memory usage cache + consumption summarizer"`

---

## Task 4: Service `/usage` endpoints + token refresh helper

**Files:**
- Modify: `service/routers/api_v2.py` (add `POST /usage`, `GET /usage`; router prefix is `/api/v1`)
- Modify: `mcp/stdio/usage-collector.js` (add `refreshAnthropicToken` used when `expiresAt` passed)
- Test: `service/tests/test_usage_api.py`

**Interfaces:**
- Consumes: `service.usage_cache.usage_set/usage_all`.
- Produces: `POST /api/v1/usage` body `{source_id, five_hour, weekly, severity, plan_type}` → stamps `updated_at` (server time) → `usage_set`; `GET /api/v1/usage` → `{pools: usage_all()}`.

- [ ] **Step 1: Failing test**
```python
# service/tests/test_usage_api.py  (use the repo's existing FastAPI TestClient fixture pattern; see service/tests/test_api_v2_regressions.py)
def test_usage_post_then_get(client):
    r = client.post("/api/v1/usage", json={"source_id": "anthropic-claude-max", "five_hour": {"used_pct": 10, "left_pct": 90}, "weekly": {"used_pct": 81, "left_pct": 19}, "severity": "warning", "plan_type": "max"})
    assert r.status_code == 200
    pools = client.get("/api/v1/usage").json()["pools"]
    p = next(x for x in pools if x["source_id"] == "anthropic-claude-max")
    assert p["weekly"]["left_pct"] == 19 and p["severity"] == "warning"
    assert "updated_at" in p
```

- [ ] **Step 2: Run — expect FAIL** (404).
- [ ] **Step 3: Implement** the two routes near the other `@router` defs in `api_v2.py` (e.g. after the `/environments/heartbeat` route). `POST` stamps `updated_at` via the module's existing `_now_iso()`/utc helper, calls `usage_set`; `GET` returns `usage_all()`. Add `from service.usage_cache import usage_set, usage_all`. Also add `refreshAnthropicToken({readCreds,writeCreds,fetchImpl})` to `usage-collector.js` (POST the OAuth refresh per the same OAuth flow; persist new token+expiresAt) with a unit test (mocked fetch) in `usage-collector.test.js`.
- [ ] **Step 4: Run — expect PASS** (pytest + node test).
- [ ] **Step 5: `ast.parse` api_v2.py + `node --check`.**
- [ ] **Step 6: Commit** `-m "feat(usage): POST/GET /usage endpoints + anthropic token refresh"`

---

## Task 5: `usageSource` auto-bind on register + override + agent-info fields

**Files:**
- Modify: `service/routers/api_v2.py` (register handler `POST /agents` ~line 12142; agent detail `GET /agents/{id}` ~12829; add `PATCH /agents/{id}/usage-source`)
- Test: `service/tests/test_usage_api.py` (append)

**Interfaces:**
- Produces: a derived `usageSource` stored in the agent's `runtime_config` JSON at register: `claude-code`→`anthropic-claude-max`; `codex`→`openai-chatgpt-codex`; `hermes`→`openai-chatgpt-codex` (default; `local-ollama` only if a provided `runtime_config.modelBaseUrl` lacks `chatgpt`); else `null`. Agent detail JSON gains `usageSource`, `poolWeeklyPctLeft`, `poolSeverity` (read from `usage_get(usageSource)`).

- [ ] **Step 1: Failing test**
```python
def test_register_autobinds_usage_source(client):
    for rt, src in [("claude-code", "anthropic-claude-max"), ("codex", "openai-chatgpt-codex"), ("hermes", "openai-chatgpt-codex")]:
        client.post("/api/v1/agents", json={"id": f"u-{rt}", "role": "coder", "runtime": rt, "cwd": "C:/x"})
        info = client.get(f"/api/v1/agents/u-{rt}").json()
        assert info.get("usageSource") == src

def test_usage_source_override(client):
    client.post("/api/v1/agents", json={"id": "u-ov", "role": "coder", "runtime": "hermes", "cwd": "C:/x"})
    r = client.patch("/api/v1/agents/u-ov/usage-source", json={"usageSource": "local-ollama"})
    assert r.status_code == 200
    assert client.get("/api/v1/agents/u-ov").json()["usageSource"] == "local-ollama"
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** a helper `derive_usage_source(runtime, runtime_config)` in `usage_cache.py` (pure, unit-tested in test_usage_cache.py too); call it in the register path to set `runtime_config["usageSource"]` if unset; add the `PATCH` route writing `runtime_config.usageSource`; in agent detail, merge `usageSource` + the pool's `weekly.left_pct`/`severity` from `usage_get`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: `ast.parse`.**
- [ ] **Step 6: Commit** `-m "feat(usage): auto-bind usageSource on register + override + agent-info fields"`

---

## Task 6: Wire collector loop into the env-bridge

**Files:**
- Modify: `mcp/stdio/server.js` (new `setInterval` collector guarded by `IS_ENVIRONMENT_BRIDGE`, modeled on `environmentHeartbeatTimer` ~line 2428-2435)

**Interfaces:**
- Consumes: `fetchAnthropicUsage`, `fetchCodexUsage` from `usage-collector.js`; `httpCall("POST","/usage", payload)`.

- [ ] **Step 1: Write the failing test** — a `node --check` + a focused unit test `mcp/stdio/tests/usage-collector.test.js` for an exported `collectOnce({fetchAnthropic, fetchCodex, post})` that calls both adapters and posts each non-null result:
```js
{
  const posted = [];
  const { collectOnce } = await import("../usage-collector.js");
  await collectOnce({
    fetchAnthropic: async () => ({ source_id: "anthropic-claude-max", weekly: { used_pct: 81 } }),
    fetchCodex: async () => ({ source_id: "openai-chatgpt-codex", weekly: { used_pct: 0 } }),
    post: async (p) => posted.push(p.source_id),
  });
  assert.deepEqual(posted.sort(), ["anthropic-claude-max", "openai-chatgpt-codex"]);
}
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `collectOnce` in `usage-collector.js`; then in `server.js` add `startUsageCollector()` that early-returns unless `IS_REMOTE && IS_ENVIRONMENT_BRIDGE`, sets a `usageCollectorTimer = setInterval(() => collectOnce({...}).catch(()=>{}), 180000)` and calls `collectOnce` once immediately; invoke `startUsageCollector()` where the other bridge timers start. Adapters use real fs/fetch defaults.
- [ ] **Step 4: Run — expect PASS** (collectOnce test) + `node --check mcp/stdio/server.js`.
- [ ] **Step 5: Commit** `-m "feat(usage): env-bridge collector loop (3-min poll, post to /usage)"`

---

## Task 7: `comms_usage` MCP tool + `quota-critical` advisory flag

**Files:**
- Modify: `mcp/stdio/server.js` (register `comms_usage` near other tools)
- Modify: `service/routers/api_v2.py` (agent detail: add `quotaCritical: bool` from `severity == "critical"`)
- Test: `service/tests/test_usage_api.py` (append `quotaCritical`)

**Interfaces:**
- Produces: `comms_usage()` → GET `/usage` + the caller's own `comms_agent_info` → text summary `pools[] (weekly left%, 5h left%, resets-in, severity) + you: <source> <left%>`.

- [ ] **Step 1: Failing test** (python) — after POSTing a `severity:"critical"` pool and a critical-bound agent, agent detail has `quotaCritical == True`. (The MCP tool itself is covered by `node --check` + a smoke import.)
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** the `quotaCritical` field (server-side, from the pool severity), and the `comms_usage` tool (calls the two GETs, formats text; advisory only — emits no side effects).
- [ ] **Step 4: Run — expect PASS** + `node --check`.
- [ ] **Step 5: Commit** `-m "feat(usage): comms_usage tool + quota-critical advisory flag"`

---

## Task 8: Consumption pass — per-agent tokens from transcripts/rollouts

**Files:**
- Modify: `mcp/stdio/usage-collector.js` (`readAgentConsumption({agentId, runtime, transcriptPath|rolloutPath})->{input,output,cache}` summing the on-disk `usage`/`token_usage`)
- Modify: `service/routers/api_v2.py` (`GET /usage/consumption` → `summarize_consumption(rows)` where rows come from the bridge-reported consumption, or computed server-side from a posted list)
- Test: `mcp/stdio/tests/usage-collector.test.js` + `service/tests/test_usage_api.py`

**Interfaces:**
- Produces: `GET /api/v1/usage/consumption` → `{by_agent, by_model, by_source, totals}` (from Task 3's `summarize_consumption`).

- [ ] **Step 1: Failing tests** — node: a fixture Claude-JSONL tail with two `usage` blocks → summed `{input,output,cache}`; python: `POST /usage/consumption {rows:[...]}` then `GET` → summary matches Task 3 shape.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `readAgentConsumption` (reuse the tail-read approach from `claude-stop-gate.js`/`adapters/claude.js`; sum `message.usage` for claude, `token_usage` for codex); the collector posts a consumption row per owned agent on each loop; the service stores the latest rows and `GET /usage/consumption` returns the summary.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `-m "feat(usage): per-agent token consumption pass + /usage/consumption"`

---

## Task 9: OLD dashboard (8800) — Pools band + Consumption section  [PRIMARY UI]

**Files:**
- Modify: `service/dashboard.html` (Analytics page `id="page-analytics"` ~line 1519; nav `showPage('analytics')`; add a Pools band + Consumption table; fetch `/api/v1/usage` + `/api/v1/usage/consumption`)
- Test: `service/tests/test_dashboard_usage.py` (string-match: the page contains the pools container id, the fetch calls, the severity classes)

**Interfaces:**
- Consumes: `GET /api/v1/usage`, `GET /api/v1/usage/consumption`.

- [ ] **Step 1: Failing test**
```python
# service/tests/test_dashboard_usage.py
from pathlib import Path
HTML = (Path(__file__).resolve().parents[2] / "service" / "dashboard.html").read_text(encoding="utf-8")
def test_pools_band_present():
    assert 'id="usage-pools"' in HTML
    assert "/api/v1/usage" in HTML
    assert "usage-pool-card" in HTML
def test_consumption_section_present():
    assert 'id="usage-consumption"' in HTML
    assert "/api/v1/usage/consumption" in HTML
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** in `dashboard.html`: add a `#usage-pools` band + `#usage-consumption` table to the analytics page; a `renderUsage()` that fetches both endpoints, renders one `.usage-pool-card` per pool (weekly% big + color by `severity`, 5h%, reset countdown, agent count) and a consumption table (agent/source/model/tokens/cost-est); call it from the analytics page's existing refresh path. Add CSS classes `.usage-pool-card.warning/.critical`. Read the live file for the exact analytics render hook + the poll cadence used by sibling panels.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Rebuild + smoke** `docker compose up -d --build && curl -s localhost:8800/health`; manually confirm the Analytics page shows the pools.
- [ ] **Step 6: Commit** `-m "feat(usage): old dashboard Pools band + Consumption section"`

---

## Task 10: NEW dashboard (8801) — port Pools + Consumption  [FINAL]

**Files:**
- Modify: `service/new_dashboard/` (the analytics/stats module; styles.css)
- Test: `service/tests/test_new_dashboard_app.py` (append string-match)

**Interfaces:**
- Consumes: same `/api/v1/usage` + `/api/v1/usage/consumption`.

- [ ] **Step 1: Failing test** (string-match in the appropriate new_dashboard JS for a `usage-pools` render + the fetch).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** the same two sections in the ES-module SPA, matching the new dashboard's render-guard patterns (signature guard like the rail fix); reuse the `/usage` shape.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Rebuild `new-dashboard` + smoke** `docker compose up -d --build new-dashboard && curl -s localhost:8801/health`.
- [ ] **Step 6: Commit** `-m "feat(usage): new dashboard Pools + Consumption port"`

---

## Final: docs + skills

- [ ] Update `.claude/skills/aify-comms/SKILL.md` Tool Map with `comms_usage` (+ mirror to `.agents/`); add a DECISIONS.md entry (advisory quota, 2 pools, oauth/usage + rollout rate_limits sources). Commit.

## Self-review notes
- Spec coverage: collector (T1-2,6), cache (T3), endpoints (T4), source binding (T5), comms_usage+flag (T7), consumption (T8), old dash (T9), new dash (T10), docs (Final) — all spec sections mapped.
- Single-worker/in-memory honored (T3 no DB). Advisory honored (T7 no gating). Old-first/new-last honored (T9 before T10).
