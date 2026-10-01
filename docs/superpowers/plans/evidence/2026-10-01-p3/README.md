# P3 evidence: the service side of aify-env-owned agents

## P3a: the C3 push, withdrawal, operator release and reset, D13 list fields

What it adds, all in aify-comms:

| file | what |
|---|---|
| `service/api_core/definition_snapshot.py` | PURE: the canonical form shared with aify-env, entry and snapshot shape, the fence, the ordering table (`PushOrder`) |
| `service/api_core/definition_push.py` | one push, in the caller's transaction: refusals, store order, per-id ownership, upsert, withdrawal |
| `service/routers/agent_definitions.py` | `PUT /environments/{id}/agent-definitions`; operator `POST .../definition-store/reset` and `POST /agent-definitions/{id}/release` |
| `service/api_core/definition_records.py` | the `definition` object on the agent list and detail (D13) |
| `service/routers/agents/rename.py` | refuses to rename a defined agent (its id is its host's file name) |

The witnesses are `service/tests/test_agent_definition_push.py`. They drive the real route and read
`agent_definitions` and `agents` back, not only the status code.

### Mutation battery

`mutations-p3a.json`, 54 mutants, run with the P2 driver:

```bash
AIFY_ENV_REPO=<aify-env checkout> MUTATE_TIMEOUT=120 \
  python docs/superpowers/plans/evidence/2026-10-01-p2/mutate.py . \
  docs/superpowers/plans/evidence/2026-10-01-p3/mutations-p3a.json
```

`mutations-p3a-result.txt` records 54/54 killed, each with the pytest test that killed it. The first
run killed 47. The seven survivors were gaps in the tests, each closed by a witness added in the same
commit:
- an invalid entry with no problem;
- an unavailable entry with the wrong reason;
- a definition naming another id;
- an agent defined again after withdrawal;
- the runtime a new agent is filed under;
- a push withdrawing another machine's ids;
- the state a released agent reads.

The driver gained one line for this battery: it now records pytest's `FAILED` lines as well as node's
`not ok`, so a pytest kill is named. P2's node battery prints no `FAILED ` lines, so its recorded
result is unchanged. Each run checked the seven mutated sources against SHA-256 hashes taken before
it; all matched.

### Held back on purpose

`service/tests/test_a_cold_start_keeps_the_agents_role.py` stays uncommitted and deselected. It is RED
by design until P3b (C5 dispositions), and `role-reset-RED-before-P3.txt` records that red.

### Suites

`suites-p3a.txt`.
