# Guided install S2: the witness table, run in containers

Command, from this checkout at e1f72e48 (clean, pushed):

    AIFY_ENV_REPO=<aify-env checkout> AIFY_WRAPPER_REPO=<aify-wrapper checkout> \
      node scripts/sandbox/install-chain-witness.mjs --env-ref 22ecc07 --wrapper-ref 3a14911 --out <dir>

Run fb4a48af, 2026-10-22, exit 0. A throwaway comms built from e1f72e48 with a key made for the run, and fresh
node:22-bookworm hosts; nothing reached the live service. Cleanup read every owned resource back absent.

| row | result |
|---|---|
| missing-key | exit 78 naming `aify-comms.credentialRef`; no registry, no launcher |
| unapproved | exit 78 naming the `--approve` digest; nothing connected |
| render-after | exit 78 with `claude: pending (CLI absent)`; the launcher's fingerprint is current |
| key-from-store | the installed bridge, no key in its environment, resolves the published key: 200 with it, 401 without |
| no-recursion | comms' installer as the entry: chain entered once, exit 0, registry hash equal across assets, claude assets applied |
| path-not-selection | codex on PATH, not selected: never run, no codex launcher |

Two earlier runs failed on the harness, not the chain: b167d7ec ran stamp.sh under MSYS_NO_PATHCONV, so the
fixture served no spec (503); in the next, key-from-store imported the bridge from the wrong directory. Fixed in
d040adbc and e1f72e48.

Not covered here: Windows hosts, codex and hermes assets, a real host aify-env, and a package service (the
dashboard's assets were run by its own lane: aify-dashboard b5ab2d1, docs/evidence/install-chain-assets-2026-10-22/).
