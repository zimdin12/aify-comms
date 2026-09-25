# Install For Oh My Pi (deprecated)

Pi support is deprecated. The code stays in the repo, but it is not maintained, and its tests run
only with `AIFY_TEST_DEPRECATED=pi`; run them first if you bring Pi back.

## What exists

- **No client install here.** `bash install.sh --client pi` exits 1 by design, because OMP's RPC
  channel is single-client and cannot take messages injected into an open Pi TUI.
- **Managed Pi needs `pi-aify` from aify-wrapper.** The service launches a managed Pi agent as
  `pi-aify --aify-agent <id>`. Render that launcher (and its `omp-aify` alias) from an
  [aify-wrapper](https://github.com/zimdin12/aify-wrapper) checkout with
  `./install.sh --client pi --endpoint http://<service-host>:8800`.
- **aify-env starts it.** aify-env offers Pi when the `pi-aify` launcher is on the PATH it was started
  with, and runs it in the directory you pick. Install aify-env with its own `./install.sh` after an
  aify-comms client install has registered the service; see [README.md](README.md#quick-start) and
  [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). Starting aify-env is the operator's call, because a
  second instance replaces the first and stops its managed agents.
- **`pi-aify` runs `omp`**, or `AIFY_PI_COMMAND` / `PI_COMMAND` when set. For managed agents, set it
  in the environment aify-env is started from: workers inherit that environment.

Managed Pi has not been verified end to end since aify-env became the host tier. Treat it as
unsupported until its tests pass again.

Pi's model and effort defaults are in the dashboard under **Settings → Advanced**; blank means no
override.
