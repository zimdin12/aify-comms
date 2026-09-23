"""Keys for agents on OTHER machines: each one names the machine it was issued to, and opens one door.

ASKED BY THE OPERATOR, 2026-09-23. An agent on another PC sends here by knowing this service's
address, and must not register here. Until now it did so with THE shared `API_KEY` -- the same key
every local bridge holds -- which let it do anything a local agent can, send as any local id, and
left the service trusting whatever it wrote about where it was.

`EXTERNAL_KEYS=pc2:<key>,laptop:<key>` gives each other machine its own key, labelled with a name the
operator chose (its machine name is the obvious one). A request carrying one:

  * is PROVEN to come from that machine -- the label is recorded on its message by the service, beside
    whatever the sender declared, so "from pc2" is a fact and "reachable at 10.0.0.9" is a claim;
  * may reach only the routes flagged `EXTERNAL_ROUTE` at their declaration, and nothing else: no
    consoles, no spawns, no deletes, no `/mcp`, no WebSocket;
  * cannot send as an agent registered here, nor as one of the service's own voices.

It MEANS NOTHING WITHOUT `API_KEY`. With no service key there is no authentication at all, so the other
machine could simply omit its key and be treated as local. The keyring is still parsed and reported,
and `/health` says `enforced: false`, because a setting that silently does nothing is the failure
this repo keeps recording.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field

from fastapi import HTTPException

from service.api_core.message_view import _registered_senders

#: The marker a route carries, as `openapi_extra`, to admit a request made with an external key. Set
#: where the route is declared, so the allowed set is DERIVED from the routes rather than listed here.
EXTERNAL_FLAG = "x-aify-external"
EXTERNAL_ROUTE = {EXTERNAL_FLAG: True}

#: A machine label names a machine the way an agent id names an agent, so it is admitted by the same
#: shape. It is shown to agents and the operator, and it must not be able to start a line of its own.
_LABEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")

#: Shorter than this is guessable; `openssl rand -hex 32` gives 64.
MIN_KEY_LENGTH = 16


@dataclass(frozen=True)
class ExternalKeyring:
    """The external keys this service accepts, and the entries it refused while reading them."""

    keys: dict[str, str] = field(default_factory=dict)  # label -> key
    #: Why each refused entry was refused, naming its LABEL or position and never its key.
    rejected: tuple[str, ...] = ()

    def match(self, presented: str) -> str:
        """The label whose key this is, or "" when it is none of them. Constant time per key."""
        if not presented:
            return ""
        offered = presented.encode("utf-8", "ignore")
        found = ""
        for label, key in self.keys.items():
            if hmac.compare_digest(offered, key.encode("utf-8", "ignore")):
                found = label
        return found

    def summary(self, *, enforced: bool) -> dict:
        """What `/health` shows. Counts, not labels: `/health` needs no key, and a list of the
        operator's machine names is not something to hand to anyone who asks."""
        return {"configured": len(self.keys), "rejected": len(self.rejected), "enforced": bool(enforced)}


def parse_external_keys(raw: str, *, reserved: tuple[str, ...] = ()) -> ExternalKeyring:
    """Read `label:key,label:key`. PURE. A bad entry grants nothing and is reported; it never stops
    the service, because one mistyped key must not take every agent on this host offline.

    `reserved` holds the keys that already mean something else here (`API_KEY`, `OPERATOR_KEY`). An
    external key equal to one of them would make a request from the other machine indistinguishable
    from a local one, so it is refused rather than guessed about.
    """
    keys: dict[str, str] = {}
    rejected: list[str] = []
    seen_keys: dict[str, str] = {}
    reserved_set = {r for r in reserved if r}
    for position, entry in enumerate(str(raw or "").split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        label, sep, key = entry.partition(":")
        label, key = label.strip(), key.strip()
        name = f"'{label}'" if _LABEL.match(label) else f"entry {position}"
        if not sep or not _LABEL.match(label):
            rejected.append(f"{name}: expected <machine-name>:<key>, where the name is letters, digits, dot, dash or underscore")
        elif len(key) < MIN_KEY_LENGTH:
            rejected.append(f"{name}: the key is shorter than {MIN_KEY_LENGTH} characters")
        elif key in reserved_set:
            rejected.append(f"{name}: the key is the same as API_KEY or OPERATOR_KEY")
        elif label.lower() in {existing.lower() for existing in keys}:
            rejected.append(f"{name}: the name is used twice")
        elif key in seen_keys:
            # Neither can be told apart from the other, so neither is believed.
            rejected.append(f"{name}: the key is the same as the one for '{seen_keys[key]}', so both are refused")
            keys.pop(seen_keys[key], None)
        else:
            keys[label] = key
            seen_keys[key] = label
    return ExternalKeyring(keys=keys, rejected=tuple(rejected))


async def refuse_external_impersonation(db, sender: str, machine: str) -> None:
    """An external key speaks for an agent on ANOTHER machine, so it may not name one that lives here.

    Registered here includes the service's own voices (`dashboard`, `aify-comms`, ...), which
    `_registered_senders` counts as known: a message from another machine signed `dashboard` would
    otherwise be drawn as the operator's.
    """
    if not sender:
        raise HTTPException(400, f"A message sent with the external key for '{machine}' must name its sender (from_agent).")
    if sender in await _registered_senders(db, [sender]):
        raise HTTPException(
            403,
            f"'{sender}' is an agent on this service, and the external key for '{machine}' speaks only for "
            f"agents on that machine. Send as the agent's own id there.",
        )


def external_route_table(routes) -> tuple[tuple[frozenset[str], "re.Pattern[str]"], ...]:
    """(methods, path regex) for every route declared with `EXTERNAL_ROUTE`.

    Walked with fastapi's own `iter_route_contexts`, which applies every include's prefix -- the plain
    `app.routes` walk collapses to a handful of lazy entries from fastapi 0.137 (see
    `service/requirements.txt`), and a table built from that would admit nothing, silently.
    """
    from fastapi.routing import iter_route_contexts
    from starlette.routing import compile_path

    table = []
    for route in iter_route_contexts(routes):
        extra = getattr(route, "openapi_extra", None) or {}
        if extra.get(EXTERNAL_FLAG) is True:
            regex, _, _ = compile_path(route.path)
            table.append((frozenset(route.methods or ()), regex))
    return tuple(table)


def route_admits_external(table, method: str, path: str) -> bool:
    return any(method in methods and regex.match(path) for methods, regex in table)
