"""PURE apart from its tallies. The C8 owners the status shadow measures, and one owner variant's tally (S2c slice 1).

An OWNER is one of the ten decisions C8 compares; the status word is the first, recorded by its own word-specific
path. A VARIANT is one law the same owner is measured under: busy under C6's strict law and under P-1, every other
owner under one. A SITE is the call site that computed today's answer, so the same owner read at two sites stays two
denominators.
"""
from collections import Counter
import copy
import re

from service.status_engine import VALID_STATUSES

OWNERS = ('status word', 'busy', 'send-time queue', 'claim', 'worker readiness', 'live counts',
          'reminder skip (B21)', 'stranded-reply guard (B26)', 'running-run promotion', 'stale-turn ceiling')
WORD = OWNERS[0]
NO_VARIANT = '-'
#: P0 C8: busy is compared "under strict and under P-1". Every owner not named here has the one variant.
VARIANTS = {'busy': ('strict', 'P-1')}
COUNT_NAMES = ('observed', 'nonEvaluation', 'evaluated', 'partialCompared', 'partialAgreed',
               'partialDisagreed', 'unavailable')
#: Sites and reasons are written by code, never by a request, and are kept to one short token so a count key stays
#: bounded and readable.
_TOKEN = re.compile(r'[a-z0-9][a-z0-9:.-]{0,63}')
UNOBSERVED = dict(instrumented=False, counts=None, bySite=None, unavailableByReason=None, disagreementsByKey=None)


def variants_of(owner):
    return VARIANTS.get(owner, (NO_VARIANT,))


def answer_token(value):
    """A comparable answer as a closed token: a boolean, or a status word."""
    if value is True or value is False:
        return str(value).lower()
    if value in VALID_STATUSES:
        return value
    raise ValueError('an answer is a boolean or a status word')


def checked_key(owner, variant, site):
    """The key a non-word observation counts under. A wrong one is a programming error, so it raises."""
    if owner == WORD or owner not in OWNERS or variant not in variants_of(owner) or not _TOKEN.fullmatch(str(site)):
        raise ValueError(f'unknown owner, variant or site: {owner!r} {variant!r} {site!r}')
    return owner, variant, site


class OwnerTally:
    """One owner variant's comparisons, per site."""

    def __init__(self):
        self._by_site = {}
        self._reasons = {}  # site -> Counter of unavailable reasons, so a reason stays with the site that gave it
        self._disagreements = Counter()  # (site, today, candidate) -> count; every part is bounded

    def record(self, site, today, candidate, reason):
        """Count one evaluation and return the names counted, for the windows."""
        if reason is not None and not _TOKEN.fullmatch(str(reason)):
            reason = 'invalid-reason'
        if reason is None:
            try:
                today, candidate = answer_token(today), answer_token(candidate)
            except ValueError:
                reason = 'invalid-answer'
        names = ['observed', 'evaluated']
        if reason:
            names += ['unavailable', f'unavailable:{reason}']
            self._reasons.setdefault(site, Counter())[reason] += 1
        else:
            agreed = today == candidate
            names += ['partialCompared', 'partialAgreed' if agreed else 'partialDisagreed']
            if not agreed:
                self._disagreements[(site, today, candidate)] += 1
                names.append(f'disagree:{today}:{candidate}')
        counts = self._by_site.setdefault(site, dict.fromkeys(COUNT_NAMES, 0))
        for name in names:
            if name in counts:
                counts[name] += 1
        return names

    def report(self):
        return dict(instrumented=True,
                    counts={name: sum(row[name] for row in self._by_site.values()) for name in COUNT_NAMES},
                    bySite=copy.deepcopy(self._by_site),
                    unavailableByReason={site: dict(reasons) for site, reasons in self._reasons.items()},
                    disagreementsByKey=[dict(site=s, today=t, candidate=c, count=n) for (s, t, c), n in
                                        sorted(self._disagreements.items(), key=lambda kv: (-kv[1], kv[0]))])
