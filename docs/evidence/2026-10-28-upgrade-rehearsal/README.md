# Upgrading a v0.5.7 host: rehearsal (2026-10-28)

The question: does a host installed before the three-repo split upgrade to 0.9 through the documented chain? It's
answered in two halves, each in throwaway containers with their own names, port, volume and network. Neither half
touches the host's `~/.claude`, `~/.aify` or the live service.

## Service: `rehearse-service-upgrade.sh` -> `service.out`

A v0.5.7 image (built from the tag) served a fresh volume. Agents, messages and a channel were seeded through its API.
Then the 0.9.11 image (cdc53ba1) started on the same volume.
- Read back: 3 of 3 seeded agents, 3 of 3 messages and the channel. No error, traceback or migration line in the new
  service's log.
- Control: the same reader ran against the v0.5.7 service before the switch and saw the seeded agents there.
- Not shown: real data. The seed was small, and a copy of a real fleet's `aify.db` is the stronger test.

## Client: `rehearse-client-upgrade.sh` -> `client.out`

The client was Debian with node 22 and the claude CLI. The CLI is there only for its MCP config; it was never logged
in or run as an agent.

1. The v0.5.7 claude install from the GitHub tag left:
   - a machine-wide `aify-comms` stdio entry plus `aify-comms-channel`;
   - four hooks;
   - `aify-comms`, `claude-aify` and `aify-doctor` on PATH.
2. Today's chain ran, at the service's own commit, against the service by IP. It took three passes, all expected:
   - the first stopped for the bridge approval digest;
   - the second stopped for the Linux channel policy and printed one `sudo` command, which was run as root;
   - the third completed.
3. After the upgrade:
   - the machine-wide `aify-comms` entry is gone and only the channel remains;
   - the hooks are replaced;
   - `claude-aify` reaches comms over `http`;
   - aify-env and herdr-aify are on PATH;
   - the registry holds `aify-comms` with its endpoint and `mcpTransport http`.
4. `aify-comms doctor` on the upgraded client gave 14 pass, 3 fail and 3 skipped (service e9ed4c25). The client key
   was accepted, and the doctor found no key-less fleet listing. All 3 failures say aify-env is not running
   (`agent-state-publishers`, `spawn-delegation`, `env-bridge`). Starting it is the operator's step, and the
   rehearsal does not take it.

`client.out` is one run at e9ed4c25. `service.out` comes from the service script before its two paths became the
`AIFY_COMMS_REPO` and `REHEARSE_DIR` variables, and ran against cdc53ba1 (0.9.11).

Earlier passes, kept only as the reasons for the rules in `docs/INSTALL_ONBOARDING.md`:
- Addressing the service as `host.docker.internal` got a 403 from `/.well-known/aify-service`. Hosts are restricted to
  IPs and localhost unless listed in `TRUSTED_HOSTS`.
- A client at a different commit than the service is refused at connect.

Not covered: hermes on Linux, `herdr-aify`, and a v0.5 environment bridge still running during the upgrade.

## Reproduce

```bash
REHEARSE_DIR=<scratch dir outside the checkout> AIFY_COMMS_REPO=<comms checkout> bash rehearse-service-upgrade.sh
REHEARSE_DIR=<scratch dir outside the checkout> AIFY_COMMS_REPO=<comms checkout> bash rehearse-client-upgrade.sh
```

Both use the image of the running `aify-comms-service` container as "today", and both tear down what they created.
