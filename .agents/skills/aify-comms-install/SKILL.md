---
name: aify-comms-install
description: Use when installing or updating aify-comms, connecting a host or client, changing endpoints or credentials, or offering optional herdr.
---

# Installing and updating aify-comms

## Inspect before asking

Read [the onboarding guide](../../../docs/INSTALL_ONBOARDING.md) from the repository root's
`docs/INSTALL_ONBOARDING.md` before changing anything. It covers the owner-to-owner install chain,
credential handling, official herdr installers and verification. Installed skills need the checkout.

```bash
bash scripts/install-state.sh --json
bash scripts/components.sh
```

Inspect the OS/shell, host role, installed clients, endpoint and the selected checkouts' versions.
The report is inventory, not a readiness verdict. `hookStates` distinguishes installed, absent and
unknown; Hermes uses the installer's profile-root resolver, not an assumed `~/.hermes`.
`apiKey=unknown` means resolution failed, not permission to generate a key. Keep secrets out of reports.
`components.sh`'s missing command or empty version is only a discovery hint, not proof of app absence.

For optional herdr, the state report searches PATH and official install locations without launching
it. If found, review its origin before `bash scripts/herdr-state.sh --probe` checks only `--version`.
No PATH entry does not mean no app. Resolve unknown locations with the operator before reinstalling.

Show a table of installed, missing, outdated and unknown components, with observed and intended
versions, evidence and proposed action. Use the doctors for running identity; a healthy port or a
present launcher does not prove it is current. A failed version lookup stays unknown.

## Ask only the gaps and the optional choice

1. Does this host serve the API/dashboard, run agents, or both? Ask only when intent is unresolved.
2. Which clients are missing from the intended set? Claude, Codex and Hermes client installs are
   supported; Pi/OpenCode client installs remain disabled.
3. Which endpoint if none is known? Reuse an existing endpoint unless a move is requested.
4. Resolve missing or conflicting credentials privately. Reuse an existing key. Offer
   `--with-api-key` only for a chosen service-authentication change, not for an unknown key.
5. Ask: "Do you want optional herdr installed or updated for terminal panes, or skip it?"
   Offer it when missing even if the required stack is complete. Record skip without blocking the
   service or agent host. Existing installation does not authorize launching it.

## Separate plan, apply and verify

- **Verify-only:** inspect and report; no pulls, installs, credential writes, launches or restarts.
- **Plan-only:** show selected versions, missing inputs, exact owner commands and restart impact;
  execute no changes. The guide distinguishes this from an installer's actual flags.
- **Apply:** install/update only the selected components using their owning repositories. Confirm
  the selected versions and any destructive/configuration changes before executing.

For clients, run the same installer for a missing or outdated integration, once per selected client:

```bash
bash install.sh --client <claude|codex|hermes> <endpoint> --with-hook
```

For a local service, preserve existing configuration; run setup only for first installation, then
stamp and rebuild when selected. A running container may still need an update.
For agent hosts, follow [aify-env's own guide](https://github.com/zimdin12/aify-env) and reviewed
repo `install.sh`; it installs/updates the package and checks registered-service credentials.
It is not a bare npm install followed by a daemon launch. aify-env's guide points to herdr's own
installer after opt-in. No comms script installs another product. aify-wrapper is already a pinned
dependency of the client installer; use its own guide for a standalone launcher-only install.

## Verify and report pending restarts

Run `aify-comms doctor` and, on agent hosts, `aify-env doctor`. Compare installed and running identity
with the selected source/version. Re-read registry/credential status without displaying keys.
Report checks passed, missing/outdated/unknown items, declined options and pending restarts separately.

Starting or restarting aify-env supersedes the current host and can reap managed workers. Treat it,
service rebuilds and wrapper relaunches as separate disruptive actions requiring approval. Installing
a new package does not update an already-running process.

Never run a bare `aify-comms` to test it; older installations can start a competing bridge. Use
`aify-comms doctor` or `--check`. Likewise, bare `aify-env`, bare `herdr` and `aify-env herdr` are
launches, not checks. Integration support is gated by the source/help and real probe evidence in the
guide, not by herdr being installed.
