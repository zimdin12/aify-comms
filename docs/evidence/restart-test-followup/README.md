# Restart test follow-up

The published `dd8c0d72` base reproduces three governing failures missed by the restart selection. The unsupported-action test still expects restart to be rejected with 400. The refusal scanner cannot recognize the starting-message assertion split into two substrings. This correction changes only those two tests, not runtime code or a coverage ceiling.

Baseline command: `python -m pytest service/tests/test_agent_control_refusals.py service/tests/test_every_refusal_is_exercised.py -q`. The sealed receipt `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/issue26-baseline-governing.txt` records three failures, 12 passes and seven passing subtests. The intended behavior was already covered by the retained restart admission test, but these governing files were missing from its original selection.

## Selecting route readers

A bounded text inventory of top-level `service/tests/test_*.py` selects files containing `/control` and either `/agents/` or `/agent-sessions/`. The existing agent-control refusal file is its positive control; `test_no_agent_names.py` is its negative control. This finds the following 18 candidate readers. It is not a compiler-resolved or exhaustive caller inventory; dynamic route construction and tests elsewhere remain outside this filter.

- `service/tests/test_a_nested_claude_does_not_stop_its_parent_agent.py`
- `service/tests/test_a_spawn_defines_its_agent_first.py`
- `service/tests/test_agent_control_refusals.py`
- `service/tests/test_agent_stop_resume.py`
- `service/tests/test_an_agent_its_environment_cannot_start_is_not_available.py`
- `service/tests/test_an_effort_change_reaches_the_start_it_is_for.py`
- `service/tests/test_api_v2_regressions.py`
- `service/tests/test_bridge_write_bodies_are_declared.py`
- `service/tests/test_claiming_the_operator_on_a_control_route_requires_the_key.py`
- `service/tests/test_hermes_remove_triad_reap.py`
- `service/tests/test_legacy_routes_delegate_defined_agents.py`
- `service/tests/test_lifecycle_phase7.py`
- `service/tests/test_no_route_accepts_an_unproven_operator_name.py`
- `service/tests/test_removed_agent_is_refused_everywhere.py`
- `service/tests/test_start_refusal_names_the_real_cause.py`
- `service/tests/test_stop_worker_interrupts_the_run_in_flight.py`
- `service/tests/test_the_switched_word_is_aify_envs.py`
- `service/tests/test_the_two_analytics_endpoints_agree.py`

For a restart-area change, run this reader population plus lifecycle request/launch tests, session control/refusal tests and the refusal-coverage gate. Include governing documentation and source checks when their inputs change. The receipt's first JSON line records every actual selected file and the command; a text match does not imply that its file ran.

## Qualification

PASSES IN TESTS: the 18 reader candidates plus lifecycle request/launch, session control/refusal, refusal-coverage, prose-path and identity checks passed 521 tests plus 310 subtests across 24 files, with eight existing Starlette warnings, in 39.23 seconds. Invocation is `python -m pytest -p xdist.plugin -n 8 --dist loadfile -q` followed by the saved population. `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/issue26-restart-followup-green.txt` contains the exact command, synthetic home and database isolation, complete output and `ACTUAL_EXIT=0`. This is targeted qualification, not a full-suite claim. No live operation is part of this test-only correction.
