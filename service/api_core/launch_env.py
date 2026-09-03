"""The environment a managed worker is launched with — composed HERE, by the tier that knows it.

WHY THIS MOVED, 2026-09-03. Every value below was composed on the HOST, in
`mcp/stdio/terminal-env.js`, by the aify-comms environment bridge. That bridge is being removed:
aify-env becomes the process host, and the operator's reasoning for it is exactly right — the
container cannot hold the agents, or they would be in the container's environment rather than the
host's.

That left one question with three bad answers and one good one. Port those 90 lines of dense
domain knowledge into aify-env (a second copy, in a second repo, of a file where every line has a
defect behind it); port them into a shared package (real, but the wrapper-template pin has already
shown how quietly a package copy goes stale); or have the tier that already composes `command` and
`argv` compose the environment too. It is the same act. `service/runtimes/*.py` already declares
`session_env_vars` per runtime and `_default_console_argv` already builds the launch, so the
knowledge is here and was being re-derived there.

WHAT THIS DELIBERATELY DOES NOT COMPOSE, because only the host can:
  - the BASE environment. A host merges this overlay over its own, and never receives one; sending
    a process environment over the wire would put whatever the sender happened to hold — including
    its secrets — on the network.
  - `CODEX_HOME`, which names a directory that must be CREATED on the machine that will run the
    process.
  - the four inherited `*_SESSION_ID` passthroughs, which are facts about the host's own process.

So the split is: the service says WHAT the worker must know, the host adds what only it can. Neither
side can compose the other's half, which is what stops this becoming two implementations again.
"""

from __future__ import annotations

from typing import Any

from service.api_core.runtime import _normalize_runtime

#: Set on EVERY managed launch, including to "". Not a default — an assignment.
#:
#: The distinction cost this project two separate bugs and they are both in the JS this replaces.
#: A host merges this overlay over its own environment, so a key merely LEFT OUT is INHERITED from
#: whatever launched that host, not absent. `AIFY_AGENT_ROLE` unset meant a worker inherited the
#: bridge's role and its self-register overwrote the spawn's — ask for a tester, get a coder, with
#: nothing reporting a problem. `AIFY_HERMES_FRESH_CONTEXT` unset meant one Reset made every later
#: spawn start fresh for ever.
#:
#: So these names are always present in the result. A test asserts the key set rather than the
#: values, because "which keys are always written" is the property that failed.
ALWAYS_SET = (
    "AIFY_RUNTIME",
    "AIFY_HERMES_FRESH_CONTEXT",
    "AIFY_AGENT_ID",
    "AIFY_COMMS_AGENT_ID",
    "AIFY_AGENT_ROLE",
    "AIFY_AGENT_CWD",
    "AIFY_SESSION_HANDLE",
    "AIFY_ENVIRONMENT_BRIDGE",
    "AIFY_MANAGED_DISPATCH",
    "AIFY_TERMINAL_ID",
    "AIFY_SESSION_MODE",
    "AIFY_MANAGED_VIA_WRAPPER",
)


def launches_via_wrapper(settings: dict[str, Any], runtime: str) -> bool:
    """Does THIS runtime's worker run inside a `*-aify` wrapper whose child bridge claims work?

    NOT `_managed_via_wrapper_for_runtime`, AND THE DIFFERENCE COST A LIVE FLEET ITS DELIVERY.
    Measured 2026-09-03: seven managed workers started, registered, and read `online`, and every
    channel dispatch to them sat `queued` for ever. This function did not exist and the launch
    composer reached for the similarly-named one, which answers a DIFFERENT question and answers it
    the opposite way for the runtime that matters:

      `_managed_via_wrapper_for_runtime` asks "should managed dispatch route through a wrapper PTY
      INSTEAD OF the native RPC adapter" -- and returns FALSE for claude-code, correctly, with the
      reason in its own docstring: claude-code is *already* wrapper-backed, so the flag is moot.

      This asks "is the process being launched a wrapper" -- TRUE for claude-code, because that is
      exactly what makes it moot above.

    `AIFY_MANAGED_VIA_WRAPPER` is read by the CHILD BRIDGE to decide whether to advertise channel
    and resident claim modes. Set to "0" for a claude-code worker, the worker comes up healthy and
    claims nothing, which is indistinguishable from a delivery bug anywhere else in the chain.

    Two functions, near-identical names, opposite answers, one of them right for each caller. The
    remedy is the name: this one says what the VALUE means rather than what a policy is called.
    """
    runtime_n = _normalize_runtime(runtime or "")
    # claude-code ALWAYS. Its worker IS `claude-aify`, and `claude-channel.js` inside it is the
    # thing that claims channel dispatches -- there is no configuration under which that is false.
    if runtime_n == "claude-code":
        return True
    # Everything else follows the routing policy, which is the question that flag was written for.
    from service.api_core.capabilities import _managed_via_wrapper_for_runtime

    return _managed_via_wrapper_for_runtime(settings, runtime_n)


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def managed_launch_env(
    *,
    terminal: dict[str, Any],
    agent: dict[str, Any] | None = None,
    workspace: str = "",
    terminal_id: str = "",
    managed_via_wrapper: bool = False,
) -> dict[str, str]:
    """The aify-owned variables a managed worker is launched with.

    PURE: a dict in, a dict out, no environment read and no filesystem. That is what lets the two
    cases that only happen when something is already wrong — a missing agent row, a runtime with no
    adapter — be tested rather than reasoned about.
    """
    agent = agent or {}
    runtime = _normalize_runtime(_text(terminal.get("runtime")) or _text(agent.get("runtime")))
    handle = _text(terminal.get("sessionHandle")) or _text(agent.get("sessionHandle"))
    runtime_config = agent.get("runtimeConfig") or terminal.get("runtimeConfig") or {}
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    runtime_state = agent.get("runtimeState") or terminal.get("runtimeState") or {}
    if not isinstance(runtime_state, dict):
        runtime_state = {}

    model = _text(agent.get("model")) or _text(runtime_config.get("model"))
    effort = _text(runtime_config.get("effort")) or _text(runtime_config.get("thinking"))
    resume_policy = _text(runtime_state.get("resumePolicy")).lower()

    env: dict[str, str] = {
        "AIFY_RUNTIME": runtime,
        # A Reset writes `resumePolicy: "fresh_context"`. Until 2026-08-31 only codex read it, so a
        # hermes Reset reported success and resumed anyway — one agent stayed on a JUNE conversation
        # until it reached 1.1M tokens against a 900k window and could no longer answer.
        "AIFY_HERMES_FRESH_CONTEXT": "1" if resume_policy == "fresh_context" else "",
        "AIFY_AGENT_ID": _text(terminal.get("agentId")) or _text(agent.get("id")),
        "AIFY_COMMS_AGENT_ID": _text(terminal.get("agentId")) or _text(agent.get("id")),
        "AIFY_AGENT_ROLE": _text(agent.get("role")) or _text(terminal.get("role")),
        "AIFY_AGENT_CWD": workspace or "",
        "AIFY_SESSION_HANDLE": handle,
        # This worker is a WORKER. A bridge flag inherited into one is how a test process once became
        # the environment bridge and reaped seven live gateway hosts.
        "AIFY_ENVIRONMENT_BRIDGE": "0",
        "AIFY_MANAGED_DISPATCH": "0",
        "AIFY_TERMINAL_ID": terminal_id or _text(terminal.get("id")),
        # Created BY aify-comms as a managed worker, not by a human running the wrapper. The inner
        # bridge reads this for its /agents register, so the service knows which it is; an
        # operator-launched wrapper has it unset and auto-detects via TTY.
        "AIFY_SESSION_MODE": "managed",
        # Only true wrapper-backed runtimes set this. Pi and OpenCode stay native managed and must
        # not make their child bridge advertise channel/resident claim modes.
        "AIFY_MANAGED_VIA_WRAPPER": "1" if managed_via_wrapper else "0",
    }
    # CONDITIONAL, and correctly so: these two are OVERRIDES. Writing "" would state that the spawn
    # chose an empty model, which a runtime cannot act on, where absence lets it use its own default.
    if model:
        env["AIFY_MANAGED_MODEL"] = model
    if effort:
        env["AIFY_MANAGED_EFFORT"] = effort

    for name in session_env_vars_for(runtime):
        env[name] = handle
    return env


def session_env_vars_for(runtime: str) -> list[str]:
    """The variables THIS runtime reads its session handle from.

    DERIVED FROM THE ADAPTER, never listed here: `service/runtimes/*.py` already declares
    `session_env_vars`, and a second list would agree with it until one of them was corrected.
    A runtime with no adapter contributes none rather than raising — an unknown runtime should
    launch with no handle, not fail to launch.
    """
    from service.runtimes import adapter_for

    try:
        adapter = adapter_for(_normalize_runtime(runtime))
    except ValueError:
        return []
    return list(getattr(adapter, "session_env_vars", []) or [])
