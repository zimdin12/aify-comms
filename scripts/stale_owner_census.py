"""Who still reaches a MOVED helper through `service.control_plane`?

The v0.5.4 decomposition moves helpers out of the control plane into leaf modules. The control plane
keeps importing what it still calls, so a consumer that reaches a moved helper through the OLD
address keeps working — right up until the carrier stops calling it, at which point the consumer
breaks for reasons unconnected to its own change.

TWO CARRIERS, and missing the second is what made this script necessary. My repoint passes handled
import lines, so the reviewer kept finding stale consumers I had reported clean:

    1. IMPORT FORM        from service.control_plane import _moved_name
                          from service.control_plane import _moved_name, _still_there
       — the second shape was invisible to a pass that replaced a single-name line verbatim.

    2. ALIAS-ATTRIBUTE    from service import control_plane as api_v2
                          ...
                          api_v2._moved_name(row)
       — not an import of the name at all. No import line mentions it, so no amount of
         import-line parsing finds it. This is the one the reviewer found fourth.

Both are reported. Neither is a production defect while the carrier still imports the name — it is a
correctness-of-ownership issue, and for private-helper tests it matters most: a test asserting a
helper's behaviour should exercise the module that owns it, or it is testing the carrier's import
list.

Usage:
    python scripts/stale_owner_census.py                 # report
    python scripts/stale_owner_census.py --strict         # exit 1 if any found
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

CARRIER = "service.control_plane"

#: Every name moved out of the control plane in v0.5.4, with its current owner.
MOVED: dict[str, str] = {}
for _module, _names in {
    "service.api_core.capabilities": [
        "_default_capabilities_for", "_managed_via_wrapper_for_runtime", "_default_console_command",
        "_environment_supports_terminal", "_has_hermes_gateway_url", "_environment_uses_windows_paths",
        "_managed_env_reachable", "_has_live_rpc_controller",
    ],
    "service.api_core.records": [
        "_agent_session_to_dict", "_environment_record_to_dict", "_terminal_session_to_dict",
    ],
    "service.api_core.dispatch_text": [
        "_render_pending_dispatch_item", "_build_pending_dispatch_subject", "_format_dispatch_state",
        "_coldstart_refusal_message", "_auto_handoff_subject_for_run", "_is_provider_rate_limit_error",
        "COLDSTART_REFUSED_PREFIX",
    ],
    "service.api_core.reply_contract": [
        "_contract_reminder_body", "_contract_list_query", "_is_operator_closed_contract",
        "_message_satisfies_reply_contract", "_contract_reminder_full_every",
        "_HANDOFF_REPLY_TYPES", "_COMPLETION_INFO_RE",
    ],
    "service.api_core.liveness": [
        "_has_live_managed_wrapper_child", "_has_live_channel_sidecar", "_has_live_terminal_session",
        "_agent_has_live_terminal", "_console_working_lease_fresh", "_claimer_lease_row",
        "_bridge_is_superseded", "CONSOLE_WORKING_LEASE_SECONDS", "CHANNEL_SIDECAR_STALE_SECONDS",
        "ACTIVE_RUN_BRIDGE_STALE_SECONDS",
    ],
}.items():
    for _n in _names:
        MOVED[_n] = _module


def _python_files():
    for dirpath, dirnames, filenames in os.walk("service"):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "data", "new_dashboard"}]
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn).replace("\\", "/")
                # The carrier legitimately imports what it still calls; the owners obviously do.
                if p == "service/control_plane.py" or "/api_core/" in p:
                    continue
                yield p


def census():
    findings = []
    for path in _python_files():
        src = io.open(path, encoding="utf-8").read()
        if CARRIER not in src and "control_plane" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue

        # aliases bound to the carrier module: `from service import control_plane as api_v2`,
        # `import service.control_plane as cp`
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "service":
                for a in node.names:
                    if a.name == "control_plane":
                        aliases.add(a.asname or a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == CARRIER and a.asname:
                        aliases.add(a.asname)

        for node in ast.walk(tree):
            # form 1: import of a moved name from the carrier (any arity)
            if isinstance(node, ast.ImportFrom) and node.module == CARRIER:
                for a in node.names:
                    if a.name in MOVED:
                        findings.append((path, node.lineno, "import", a.name, MOVED[a.name]))
            # form 2: alias-qualified attribute access
            elif isinstance(node, ast.Attribute) and node.attr in MOVED:
                base = node.value
                if isinstance(base, ast.Name) and base.id in aliases:
                    findings.append((path, node.lineno, f"{base.id}.attr", node.attr, MOVED[node.attr]))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if any stale consumer is found")
    args = ap.parse_args()
    findings = census()
    print(f"moved-name population: {len(MOVED)} names across "
          f"{len(set(MOVED.values()))} owner modules\n")
    if not findings:
        print("no stale owner consumers found (both import and alias-attribute forms checked)")
        return 0
    print(f"{len(findings)} stale consumer(s):\n")
    for path, lineno, form, name, owner in sorted(findings):
        print(f"  {path}:{lineno}\n      [{form}] {name}  -> should come from {owner}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
