# Usage / Quota Tracking + Stats Screen — Design

**Status:** approved architecture (Section 1), spec for review.
**Date:** 2026-06-26
**Branch:** `feature/usage-quota-stats`

## Goal

Surface, per **quota pool** (billing source), how much subscription quota remains — so agents and the operator can see when a pool is about to run out and route work to a pool with headroom. **Advisory only** (aify-comms never auto-reroutes; it surfaces + flags). Plus revamp the stats screen with per-session and fleet **consumption** data.

## Background (verified 2026-06-26)

Two quota **pools**, both subscription plans:
- **`anthropic-claude-max`** — claude-code agents. Authoritative % via `GET https://api.anthropic.com/api/oauth/usage` (bearer from `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`, header `anthropic-beta: oauth-2025-04-20`). Returns `five_hour.utilization`, `seven_day.utilization`, per-model (`seven_day_opus`/`seven_day_sonnet`), and `limits[]:{kind,group,percent,severity,resets_at,is_active}`. `utilization` = % USED.
- **`openai-chatgpt-codex`** — codex **and** hermes (hermes active config `AppData/Local/hermes/config.yaml`: provider `openai-codex`, base_url `https://chatgpt.com/backend-api/codex`). Authoritative % already snapshotted in each codex rollout: `rate_limits:{primary:{used_percent,window_minutes:300,resets_at}, secondary:{used_percent,window_minutes:10080,resets_at}, plan_type}` (primary=5h, secondary=weekly).
- (`local-ollama` — only if a hermes agent is pointed at the local model; no quota.)

Per-agent **consumption** (attribution) from each agent's own file: Claude JSONL `message.usage{input_tokens,cache_creation_input_tokens,cache_read_input_tokens,output_tokens}`+model; Codex rollout `token_usage{input_tokens,total_tokens}`.

## Architecture (approved)

Collector runs in the **host env-bridge** (`mcp/stdio`): it already runs continuously, already reads transcripts/rollouts, and is the only component with host-cred access (the Docker service cannot read `~/.claude`/`~/.codex`). One poll per pool, not per agent.

```
env-bridge collector (~3min loop)
  ├─ anthropic adapter  → GET oauth/usage (refresh token if expired)
  ├─ codex adapter      → read latest rollout rate_limits
  └─ POST /api/v1/usage {source_id, five_hour_pct, weekly_pct, *_resets_at, severity, plan_type}
service (single-worker)
  ├─ _USAGE_CACHE (in-memory, mirrors _LIVE_STATE_CACHE; + thin rolling history for sparklines)
  └─ consumption pass (slower) → per-agent token totals from transcripts/rollouts
surfacing (all read cache)
  ├─ comms_usage MCP tool
  ├─ comms_agent_info: + source, pool %, own tokens
  ├─ quota-critical flag (advisory badge; NEVER gates comms_send)
  └─ dashboard stats screen (Pools band + Consumption section)
```

## Data model

- **Source binding (per agent):** `runtime_config.usageSource` on the agent record. Auto-set at register: claude-code→`anthropic-claude-max`; codex→`openai-chatgpt-codex`; hermes→detect from its config `base_url` (chatgpt→`openai-chatgpt-codex`, else `local-ollama`). Overridable via a dashboard **Set source** control + `PATCH /agents/{id}` (mirrors the existing Set-handle pattern). No new table.
- **Usage cache (in-memory, per pool):** `{ source_id, five_hour: {pct, resets_at}, weekly: {pct, resets_at}, severity, plan_type, per_model?, updated_at, stale: bool }`. `stale=true` when `updated_at` older than ~2× poll interval (display dims, never throws).
- **Rolling history (per pool):** last ~120 samples `{ts, weekly_pct, five_hour_pct}` for a sparkline. In-memory ring; not persisted (YAGNI — sparkline is cosmetic).
- **Consumption (per agent):** `{ agent_id, source_id, model, input_tokens, output_tokens, cache_tokens, est_cost_usd, window }` for windows `session` (current) and `today`. Computed on demand / on a slow timer; cached.

## Components / files

- **Create `mcp/stdio/usage-collector.js`** — pure adapters + loop. `fetchAnthropicUsage(creds)`, `fetchCodexUsage(rolloutDir)`, `pctLeft(util)=100-util`, `severityFor(pct, providerSeverity)`. Unit-testable with injected fetch/fs (mirror `claude-stop-gate` test style).
- **Wire into `mcp/stdio/server.js`** env-bridge path — start the collector loop alongside the existing reconcile/detector loops; gate on `IS_ENVIRONMENT_BRIDGE`.
- **`service/routers/api_v2.py`** — `POST /usage` (collector ingress → `_USAGE_CACHE`), `GET /usage` (pools snapshot for dashboard + tool), `_usage_cache_get/set`, the consumption pass, and `usageSource` in the register/PATCH paths + `comms_agent_info` payload. Keep cache in-memory (single-worker invariant).
- **`mcp/stdio/server.js`** — `comms_usage` MCP tool: returns `{pools:[...], me:{source, pool, consumed}}`.
- **`service/dashboard.html` (OLD dashboard, 8800) — PRIMARY UI, built FIRST** (operator's daily driver): a **Pools** band + **Consumption** section on the stats screen.
- **`service/new_dashboard/` (NEW dashboard, 8801) — FINAL PHASE, after the old dashboard works**: port the same Pools band + Consumption section to the ES-module SPA. Both dashboards read the identical `/usage` + `comms_agent_info` data — only the rendering differs.
- **Config:** thresholds in settings — `usage_warn_pct` (default 90), `usage_critical_pct` (default 98); also honor the provider's own `severity` and a `rate_limit_reached` wall.

## Surfacing details

- **`comms_usage`** → pools (each: name, weekly% used + left, 5h% used + left, resets-in, severity, #agents) + the caller's own source + consumed tokens.
- **`comms_agent_info`** gains `usageSource`, `poolWeeklyPctLeft`, `poolSeverity`, `ownTokens`.
- **Status:** a `quota-critical` boolean on the agent when its pool ≥ critical threshold — surfaced as a dashboard badge + available to managers; **does not** change `derive()` or gate sends (advisory, per decision A).
- **Stats screen (C):** Top **Pools** band — one card per pool: weekly % (large, color by severity), 5h %, reset countdowns, agent count, sparkline. Below, **Consumption**: a per-agent/session table (agent, source, model, tokens in/out/cache, est cost, last active) + fleet rollups (tokens today, by model, by pool).

## Error handling

- Anthropic token expired → refresh via the OAuth refresh token (same flow the CLI uses); on refresh failure, mark pool `unknown` (not 0%).
- Codex rollout missing/old → use last known + `resets_at`; mark `stale` past the window.
- Pool with no quota adapter (`local-ollama`) → show "local · no limit", never a fake %.
- Collector never throws into the bridge loop; a failed poll logs + keeps the last cached value.
- All consumers tolerate a missing/stale cache (dim the panel, no error).

## Testing

- `mcp/stdio/usage-collector.test.*`: anthropic-shape → pct/severity; codex rate_limits-shape → pct; token-refresh path; missing-file → unknown; `pctLeft`/`severityFor` table tests.
- Python: `POST/GET /usage` round-trip into `_USAGE_CACHE`; `usageSource` auto-bind on register (claude/codex/hermes); consumption summation from a fixture transcript; threshold → quota-critical.
- Dashboard: string-match tests (existing pattern) for the Pools band + Consumption section presence and the `comms_usage`/`/usage` wiring.

## YAGNI / ponytail cuts (explicit)

- **No** generic multi-provider plugin framework — exactly 3 adapters, 2 with quotas.
- **No** per-agent failover *within* a pool (codex & hermes share fate).
- **No** enforced routing / send-gating (decision A = advisory).
- **No** persisted history table (in-memory ring; sparkline is cosmetic).
- **No** budget modeling for local/Ollama (no cap exists).
- **No** direct ChatGPT-backend usage poll in v1 (rollout `rate_limits` + `resets_at` suffice; add later only if idle-pool staleness bites).

## Build order (phases)

1. **Collector + adapters** (`usage-collector.js` + tests) — pure, no wiring. Verify against live `oauth/usage` + a real rollout.
2. **Service ingress + cache** — `POST/GET /usage`, `_USAGE_CACHE`, thresholds. Tests.
3. **Source binding** — auto-set `usageSource` on register (claude/codex/hermes-config-detect) + override path + `comms_agent_info` fields. Tests.
4. **Collector wiring** into the env-bridge loop (gated on `IS_ENVIRONMENT_BRIDGE`).
5. **`comms_usage` tool** + the `quota-critical` advisory flag.
6. **Consumption pass** — per-agent token totals from transcripts/rollouts.
7. **OLD dashboard (8800) stats screen** — Pools band + Consumption section. **(operator's primary surface — must work end-to-end here first.)**
8. **NEW dashboard (8801)** — port the same two sections. **Final phase, only after 7 is validated.**

Backend (1–6) is dashboard-agnostic and serves both UIs.

## Out of scope (v1)

Multi-host pools (one env-bridge/host today); multiple accounts per provider; cost-accurate $ (rough estimate only); historical usage analytics beyond the live sparkline.
