# Triage of the external review of 0.8.4 (2026-10-04)

The review judged three HIGHs from the round before and raised new MEDIUMs. Every claim was checked at source
before triage by four read-only investigators (aify-comms `cf4f5710`, aify-env `ba486a4`/`84a02ae`, aify-wrapper
`9470225`). Probes ran on scratch copies only. Each fix below is its own commit, red on its predecessor and
mutation-proven. comms-senior-dev reviews each one.

## Fixed in 0.8.5

| # | Finding | Evidence (as read) | Fix |
|---|---|---|---|
| F1 | Re-register rewrites a DEFINED agent's model/effort without the key, and a new terminal on its session runs them | `agent_registration_writes.py:208,223` writes `model`/`runtime_config`; `terminals.py:228-236` launch read the agents row; `managed_pty_for_dispatch.py` opens terminals from dispatch, console input and session-mode gates | comms `272df43f`: a definition-bound launch takes model/effort from its spawn spec (`as_its_spec_declares`) |
| F2 | The environment and session-mode routes rewrite an UNDEFINED agent's model, effort, workspace and mode without the key | `environment_assignment.py:95-97,196-225`; `session_mode.py:107-109` proved only the defined branch; Steven's 2026-10-02 ruling (DECISIONS.md) | comms `08606578`: both routes prove the operator for every agent |
| F3 | A spawn or definition env can set `HARNESS_EXTRA_ENV` (forces `HERMES_SESSION_ID`), `HARNESS_ENDPOINT`, `HARNESS_ROLE` | `spawn_env.py:47,63` and `definition_schema.py:23,74` reserved `AIFY_` only; hermes template exports extra env before reading the session id (`hermes-aify.sh.in:240-271`) | comms `308facbe`, env `d1ec2c3`, wrapper 0.8.4: `HARNESS_` reserved beside `AIFY_`; hermes `CLAUDE_MCP_SERVER_URL` is the launcher's endpoint |
| F4 | Two concurrent Starts queue two `replace` spawns | `session_ops.py` read live/pending with no write lock; reproduced in a scratch harness (two spawn rows) | comms `0a93c4e2`: `BEGIN IMMEDIATE` before the start's reads; Start dialog in-flight guard |
| F5 | Start answers `alreadyRunning` for a session whose terminal exited | `session_ops.py` read the row only; `_live_session_for` excludes `managed_sessions_with_dead_terminals` | comms `0a93c4e2`: same exclusion in the start gate |
| F6 | A non-UTF-8 file name fails every definition-store call | `#scan` rethrows `ENOENT` for the U+FFFD-decoded name; reproduced on Windows and Linux | aify-env (store slice): the name is listed raw and reported invalid |
| F7 | A local clone hard-links both sides, so the ORIGINAL repo is refused, and the message says re-clone | `git-dir-contents.mjs:126,192` (`nlink > 1`), message `:49`; reproduced on scratch repos | aify-env (store slice): the message names both sides; the maintenance advice is conditional (see senior-dev's note) |
| F8 | Spawn and terminal claims need no host proof; PATCH spawn skips the bridge check when `bridgeId` is omitted | `spawn_requests_io.py:115-116`, `terminal_controls_io.py:79-86`, `spawn_requests.py:448` | comms `f97f08da`; the env doctor tells a 401 from a host-proof 403 (store slice) |
| F9 | KNOWN_ISSUES says "0.8.2 fixes its HIGHs and MEDIUMs" and the carry-overs "were not traced" | `KNOWN_ISSUES.md:10,41-43` | corrected with this round |

Also from comms-senior-dev's review of 0.8.5 in the same round:
- removal history matched case-sensitively (comms `78052e25`);
- a refusal beside a freed id was lost on a failed retry (env `beb4b14`);
- `attach`/`run`/`import` chose from an incomplete receipt look (env `3f1e1ec`);
- the store-lock takeover (R1; env `84a02ae`, in independent review).

## Not fixed: limits stated, with who owns them

- **D1, host proof is trust-on-first-use (Steven's design, 0.8.2).**
  - A machine that never presented a proof is open.
  - Whoever presents first owns it; the real host then gets 403 until an operator reset.
  - With `OPERATOR_KEY` unset (the 2026-09 ruling: unset means no operator gate), `POST /host-proofs/{m}/reset`
    is open to any API-key holder. So the proof gives NO protection against a hostile API-key holder; it guards
    only against stale or misconfigured hosts.
  - On this host `OPERATOR_KEY` equals `API_KEY`, so the operator gate is the API key.
  - Protection against hostile key holders needs an enrollment the operator approves. That is a redesign, and it
    is Steven's call.
- **D2a, program selection through plain env (to Steven).** `HERMES_COMMAND` (`hermes-aify.sh.in:278`) and
  `PI_COMMAND` pick the program the launcher runs, and neither prefix is reserved. An API-key holder who spawns an
  UNDEFINED agent can therefore choose the binary, past aify-env's launcher allowlist (traced, not run). The same
  key can already ask any agent with shell tools to run commands, which is why this is a stated limit rather than
  a fix. Steven decides whether spawn env should be narrowed further.
- **D2b, model through plain env: no escalation found.** `HERMES_INFERENCE_MODEL`/`HERMES_MODEL` make the hermes
  launcher treat the model as given (`:385`).
  - A caller's spawn env never reaches a DEFINED agent: a direct spawn of a defined agent is refused, 409
    (`spawn_requests.py:266-268`), and its starts are built from the definition.
  - A definition's own env is the host's file, or an operator-gated change request.
  - For an UNDEFINED agent the spawner already chooses the model (the spawn's `model` field).
- **D3, "four small launcher items".** The earlier findings file is not on this host, so they are UNKNOWN until
  it is recovered.
- **Hard-link refusal (narrower rule).** Accepting a second-named object once its content is proven to match its
  name (60-100 lines) is deferred. The refusal stays and its message is corrected (F7).
- **The env export gate does not walk `lib/plugins/**`.** 17 plugin exports are named by no test. This is a gate
  scope defect, scheduled after 0.8.5 (senior-dev handoff).
