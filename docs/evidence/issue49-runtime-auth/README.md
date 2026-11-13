# Runtime authentication in the install spec

2026-11-13. This is the auth-only portion of #49, not completed fresh-install or E1 qualification.

The public discovery route projects `credentialRef.required` from the running service's configured API key.
The stamped document, artifact hashes, manifests and probe contract are unchanged. Build-host credentials do not
decide the requirement. The original spec validation still runs first and refuses an absent, stale or invalid stamp.

## Reproduce

From the repository root, with the declared test dependencies:

```bash
python -m pytest service/tests/test_comms_serves_its_registration_spec.py service/tests/test_api_key_middleware.py service/tests/test_exact_auth_path_compatibility.py -q
node --test mcp/stdio/tests/service-spec.test.js
```

The new regression drives the real ASGI application without starting its lifespan or a listening server. A fresh
fixture database supports the real `/api/v1/agents` probe. It crosses keyed/keyless runtime configuration with both
stamped values. In each arm it checks the declared requirement, missing/wrong/valid key responses, unchanged
`GET /api/v1/agents` plus `x-api-key`, unchanged artifact metadata and exact manifest bytes, no stamp rewrite, and
absence of the planted fixture key from discovery output.

## Observed results

PASSES IN TESTS, synthetic isolated homes only:

- Before the product fix, the new regression plus existing stamp-refusal controls exits 1: two subcases fail at
  `runtime auth, not stamped auth`, with 6 parent tests and 6 subtests passing. The failures are keyless runtime with
  stamped required=true, and keyed runtime with stamped required=false.
- After the fix, the three-file Python command above exits 0: 48 tests and 218 subtests pass. Existing TestClient
  deprecation and duplicate-operation-ID warnings remain.
- The builder/manifest Node file exits 0: 6 pass, no failures, skips or cancellations.
- The selected size, import, layer, runtime-boundary, refusal, fixture and documentation gates exit 0:
  55 tests and 15 subtests pass.

Initial Node and prose-path runs lacked the wrapper dependency in the new worktree. Node had one missing-package
failure and five passes; Python had one unresolved dependency-path failure and 54 passes. Package and lock bytes
were compared with an existing owned checkout before copying its wrapper package into this isolated worktree.
No npm installation or host product installation was performed. Those initial failures remain in the author record;
no product change or gate weakening was needed to correct that test setup.

The retained raw author receipts are named `issue49-auth-red.txt`, `issue49-auth-green.txt`,
`issue49-auth-node-materialized.txt` and `issue49-auth-governing-materialized.txt`. They are test execution records,
not evidence of a live deployment, a successful connect/publication, sibling integration or a native install.

The dated E1 consent amendment is in `docs/roadmap/e2e-staging.md`. No new E1 run was made for this change.
