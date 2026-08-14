"""Why an environment cannot host a Console for a given runtime, said precisely.

Extracted from `start_session_console` in `service/routers/sessions.py` in v0.5.4;
`test_start_session_console_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the message literals are preserved
byte-for-byte.

THE MESSAGES ARE THE POINT. Both branches refuse with 409, and an operator reading only the status
code learns nothing they can act on. The split matters: whole-environment PTY capability being off is
a HOST problem (node-pty is not installed or built for that bridge, and the Console is dead there for
every runtime), while an advertised-runtimes miss is a SELECTION problem (the host is fine, this
runtime is not on its list). Those have different fixes, and collapsing them into one "not supported"
sent operators to reinstall a bridge that was working.
"""
from __future__ import annotations

from fastapi import HTTPException

from service.api_core.capabilities import _environment_supports_terminal


def _refuse_console_without_terminal_capability(environment, session) -> None:
        """Raise the 409 that explains WHICH capability is missing, or return and let the start run.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Both arguments
        are passed under the caller's own names for the same reason: inline-back does not substitute
        arguments.
        """
        if not _environment_supports_terminal(environment, session["runtime"]):
            env_id = environment.get("id")
            if not bool(environment.get("terminal")) or not bool(environment.get("pty")):
                # Whole-environment PTY capability is off — not a per-runtime
                # issue. The bridge on that host reports no terminal/pty
                # (usually node-pty is not installed/built there).
                detail = (
                    f'Environment "{env_id}" has no PTY/terminal capability — its bridge reports '
                    f'terminal={bool(environment.get("terminal"))}, pty={bool(environment.get("pty"))}. '
                    f'This blocks the Console for ALL runtimes there (not just "{session["runtime"]}"). '
                    f'Fix: install/build node-pty for the aify-comms bridge on that host '
                    f'(reinstall via install.sh and restart the bridge), then retry. '
                    f'Use an environment that advertises terminal support in the meantime.'
                )
            else:
                advertised = ", ".join(
                    str(r) for r in (environment.get("terminalRuntimes") or [])
                ) or "none"
                detail = (
                    f'Environment "{env_id}" supports the Console but not for runtime '
                    f'"{session["runtime"]}". It advertises terminal runtimes: {advertised}. '
                    f'Spawn/select a supported runtime, or update that bridge.'
                )
            raise HTTPException(409, detail)
