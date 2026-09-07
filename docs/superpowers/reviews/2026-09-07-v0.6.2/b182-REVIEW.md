# b18298b7 bounded P1 review

Verdict: REVISE. Native outbound authentication now works, and terminal claim ordering plus the two test gaps are narrowly closed. R2/R3 and redirect handling remain blockers.

Subject: b18298b76948752d27f8abbc6fe5c169af55c3bd, detached `b182-source`.
Range: eb12fbd83a4eb827b185b754e345ffa2a1f8433d..b18298b76948752d27f8abbc6fe5c169af55c3bd. Exact-range diff check exit 0.

## P1: endpoint binding ends before failover

`mcp/stdio/aify-service-endpoint.mjs:129-135` resolves the registry credential for SERVER_URL. Lines 174-178 add fallback URLs. Lines 228-245 attach the single credential before iterating those destinations.

Real synthetic receivers and sealed child environments establish:
- Matching primary receives the synthetic key.
- Foreign primary receives no key.
- Foreign CLAUDE_MCP_SERVER_URL overriding matching AIFY_SERVER_URL receives no key.
- Matching primary returns 503; configured foreign fallback receives the registry credential and returns success.

The direct mismatch is closed, but R2 is not. Explicit fallback selection is not registry authorization to disclose that stored key there. Resolve/authorize each actual destination before sending, retaining source identity rather than flattening the credential into one string. Redirects also need explicit fail-closed policy.

## P1: secure-reader ACL checks are reachable and omitted

`mcp/stdio/registry-credential.mjs:195-203` checks path and bytes but does not check file security.

A real temporary Windows credential was written using aify-env's secure writer. Both readers accepted it. Granting Everyone read access to that synthetic file using icacls made aify-env return CREDENTIAL_INSECURE, detail `readable by Everyone (a group)`. The comms reader still returned the synthetic credential. The ACL grant was removed in the completed probe.

The missing ACL/ownership contract is not unreachable. aify-env's public readCredentialFile calls inspectCredentialFile on every read. Dependency unavailability does not waive the boundary. Share/package the complete implementation, securely resolve at a suitable asynchronous launch boundary, or refuse unsupported custody checks. No symlink conclusion is claimed.

## Doctor redirect claim is false; authenticated redirect discloses the key

`mcp/stdio/doctor.js:545-565` uses fetch without redirect: 'manual' or 'error'. Fetch follows redirects before credentialPolicyFrom sees the response.

Executed the exact extracted gatherClientApiKeyEvidence function with its actual verdict helpers and real synthetic receivers, not the full live doctor:
- Primary 302 to unrelated 200 becomes no-key-required with ok true, attributed to the primary.
- Primary unauthenticated 401, authenticated 302 to unrelated 200 becomes authenticated with ok true. The unrelated receiver gets X-API-Key.

This contradicts the packet's claim that redirect produces unknown. Keep redirects un-followed and unverified, including authenticated probes. The config parser's removal is sensible, but one doctor process authenticating still does not certify every standalone worker's effective environment.

## Narrow closures

The shipped removal/claim focus passed 12 tests, exit 0. The restored original selection rank fixes the RETURNING order regression.

Independent isolated mutants, each run against the six-test claim file:
- Remove rank restoration: only test_THE_CLAIM_PRESERVES_THE_ORDER_THE_HOST_WILL_APPLY fails; 1 failed, 5 passed, exit 1.
- Remove UPDATE pending predicate: only test_A_ROW_ANOTHER_CLAIMER_TAKES_MID_FLIGHT_IS_NOT_REPORTED_AS_OURS fails; 1 failed, 5 passed, exit 1.
- Remove final claim commit: only test_THE_CLAIM_IS_DURABLE_not_just_returned fails; 1 failed, 5 passed, exit 1.

Source bytes restored after mutations; git diff --exit-code on the production subject file returned 0. Initial mutation-driver cardinality guard stopped before attempting the commit mutant because the unscoped commit token occurred twice. The corrected driver scopes the claim commit. An initial security-probe assertion used the wrong spelling for the typed refusal and stopped after observing the ACL failure; the corrected completed run retained the exact CREDENTIAL_INSECURE token.

R4: native comms_send succeeded, dispatch run_1788817505193_5a292f35 has a delivered/completed event. This proves the requested native outbound operation from this session. It does not prove fleet convergence or recipient comprehension.

## Evidence and limits

Evidence root: C:/Users/Administrator/AppData/Local/Temp/aify-v062-review/
- b182-security-probe.mjs, b182-security.log
- b182-doctor-redirect.mjs, b182-doctor-redirect.log
- b182-mutations.py, b182-mutation-results.json, b182-mutant-{order,predicate,commit}.log

Only synthetic credentials/receivers/homes were used in attack probes. No production edits, deployment, install, fleet action, or tag operation. This was the requested short blocker pass, not a full suite rerun or complete renewed release review. R7-R11 and remaining docs findings are not closed. No fleet-restart or release approval.
