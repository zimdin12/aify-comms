"""Which aify-env builds the status shadow's observations depended on, and which read the delivery-turn fold (B6a).

A window may qualify only on ONE known env build. So every build that could have shaped an evaluation is collected
at the event, never sampled at a tick:
  - each observation contributes its OWN frozen binding (every publisher's data build and every held record's origin
    build) in the same synchronous call that adds its counts, agreements and unknowns alike;
  - each applied data publication contributes its build;
  - take() returns the interval's set and reseeds it from what the mirror holds at that instant, so the next interval
    starts with every build still in effect.
An absent build, a lost view and a binding the observer meant to hold but lost are all 'unknown', which can never
qualify. None means the observer consulted no env view at all.

THE FOLD'S HISTORY HAS NO PROVENANCE (reviewed choice, option iii). The delivery-turn fold keeps knowledge across
publications and windows, and nothing records which build set it. Every evaluation of an owner whose candidate reads
the fold marks that owner, and a marked owner is never complete in that window, whatever its builds say. Only a
separately reviewed fold-provenance slice may lift this; B6 does not.
"""
import json

from service.api_core.shadow_owners import OWNERS

UNKNOWN_BUILD = 'unknown'
LOST = object()  # the observer meant to consult an env view and holds none: unknown, never "consulted nothing"
#: The owners whose candidates read the delivery-turn fold (comms_view and busy_consumers). Used only to read a window
#: whose fold record is missing or malformed: every one of them is then refused.
FOLD_OWNERS = ('busy', 'send-time queue', 'claim', 'reminder skip (B21)', 'stranded-reply guard (B26)')


def as_json(values):
    """A set as stored on a window: canonical sorted JSON."""
    return json.dumps(sorted(values), separators=(',', ':'), ensure_ascii=False)


def parse_set(stored):
    """PURE. A stored set back as a frozenset of strings, or None when missing or malformed."""
    try:
        values = json.loads(stored)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        return None
    return frozenset(values)


def env_coherent(stored):
    """PURE. A window qualifies on its env builds only when it saw exactly one, and that one is known."""
    builds = parse_set(stored)
    return builds is not None and len(builds) == 1 and UNKNOWN_BUILD not in builds


def parse_owners(stored):
    """PURE. A stored fold record as owner names, or None when it is missing, malformed or names an unknown owner."""
    owners = parse_set(stored)
    return owners if owners is not None and owners <= set(OWNERS) else None


def fold_unverified(stored):
    """PURE. The owners whose fold reads a window recorded; every fold owner when the record cannot be read."""
    owners = parse_owners(stored)
    return frozenset(FOLD_OWNERS) if owners is None else owners


def origin_build(record):
    """The build of the publication that set this record, else 'unknown'."""
    origin = record.origin
    return (origin[4] if origin is not None and len(origin) > 4 else None) or UNKNOWN_BUILD


def builds_of(binding):
    """PURE. The builds a frozen binding's view holds: each publisher's data build and each record's origin build."""
    if binding is None:
        return frozenset()
    if binding is LOST or binding.loss or binding.view is None:
        return frozenset((UNKNOWN_BUILD,))
    builds = set()
    for publisher in binding.view.values():
        builds.add(publisher.build or UNKNOWN_BUILD)
        if publisher.loss:  # an unrecovered loss: what that publisher would have applied since is not known
            builds.add(UNKNOWN_BUILD)
        builds.update(origin_build(record) for record in publisher.rows)
    return frozenset(builds)


class Provenance:
    """The builds and fold readers recorded since the last take(). Every method is synchronous and never raises."""

    def __init__(self):
        self._builds = set()
        self._fold = set()

    def observe(self, owner, binding, fold):
        try:
            self._builds |= builds_of(binding)
        except Exception:
            self._builds.add(UNKNOWN_BUILD)
        if fold:
            self._fold.add(owner)

    def applied(self, projection):
        """A publication the receiver applied to the mirror; None when it could not be read (the view was lost)."""
        try:
            self._builds.add(UNKNOWN_BUILD if projection is None else projection.build or UNKNOWN_BUILD)
        except Exception:
            self._builds.add(UNKNOWN_BUILD)

    def take(self, seed_binding):
        """(builds, fold owners, seed) since the last take. The builds reseed from `seed_binding`, the mirror now."""
        try:
            seed = builds_of(seed_binding)
        except Exception:
            seed = frozenset((UNKNOWN_BUILD,))
        taken = frozenset(self._builds), frozenset(self._fold), seed
        self._builds, self._fold = set(seed), set()
        return taken
