# S5: native consent, readiness reporting, and the live test

Part of [the guided-install plan](../2026-10-20-guided-install.md). Answers R4.

## The channel warning

- **Effective branch: UNKNOWN until a scoped native result exists.**
  - The result names the Claude Code version, account type, platform and policy source, and shows a relaunched
    resident reaching its first channel wake without the warning.
  - A managed-settings file or an installed plugin is configuration, never proof. With either present and no result,
    the report reads `channel prompt: pending native confirmation`.
  - The operator's approval of one experiment, and admin consent for one settings file, are not standing authority to
    accept future development-trust warnings.
- **Branch A** (allowlisted plugin) is selected only by that result, for the runtime, account and platform it covers.
- **Branch B** (aify-env or herdr-aify presses Enter on the warning) needs the operator's own scoped opt-in. It is
  bound to:
  - a launch this aify-env or herdr-aify started, by pane id and launch generation;
  - the exact warning text, on that generation's first screen.

  Anything else gets no keystroke.

## Readiness, reported in four lists

1. Installed and configured: packages, registry entries, credential references, launchers.
2. Running identity: which build each running daemon and agent actually runs (the doctors).
3. Native proof: a native invocation or wake observed on this platform.
4. Pending: restarts, relaunches and trust prompts the operator must take.

- Service-only and agent-host roles each get only the checks that apply to them.
- No list is "done" on doctor green alone.
- A Linux container's result says nothing about native Windows, and does not lift the current client-install hold.
- The chain never rebuilds a service, supersedes a daemon or relaunches a client. Each of those is listed as pending.

## Task #17 (dashboard-manager's disposable container)

Bound before any live registration:
- the target (live comms and dashboard URLs);
- the exact disposable identities (machine id and every agent id, listed in advance);
- the removal scope: those ids, through the normal remove;
- who removes them, and when.

A name that only looks like a test is not authority.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| branch A is not inferred | the settings and plugin are present with no native result, and the report says pending and the launcher keeps the dev flag | the branch reads A |
| branch B binding | Enter goes to the matching warning in an owned current-generation pane. The same text in an unowned pane, the same text in a stale generation, and non-matching text in an owned pane get no keystroke | any of the three gets Enter |
| B needs opt-in | without the opt-in, an owned pane with the warning gets no keystroke | it does |
| readiness | a run with a stale daemon lists it under pending and is not reported done | it is reported done |
