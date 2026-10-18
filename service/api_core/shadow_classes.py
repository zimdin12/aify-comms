"""PURE. Which known cause explains a status-word disagreement (status-switch plan S2b).

A CLASS is a named predicate over the recorded inputs of BOTH sides: comms' projected status inputs and word, and
aify-env's projected record and word. Its label says what kind of difference it is:
  - a B-gap: aify-env does not have the input yet (B2 to B6), so the difference is expected until that slice lands;
  - `bug`: one side is wrong, and it is fixed and measured again;
  - `policy:<ruling>`: an approved change the operator ruled on.

A disagreement is attributed to the ONE class it satisfies. One that satisfies none is `unresolved:unmatched`, and one
that satisfies more than one is `unresolved:ambiguous`: a cause is never chosen between candidates, and an
explanation that matches is not proof, so both stay visible as unresolved and block the switch.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DisagreementClass:
    name: str
    label: str
    applies: Callable[[dict, str, dict, str], bool]


def _comms_only_stop(comms, comms_word, env, env_word):
    """comms shows a stop aify-env never heard of: stops are recorded only in comms (B2).

    `disabled` need not be checked: a compared comms `stopped` is always derive()'s `disabled` (an explicit stop, or
    wake disabled), since a manual stop is never evaluated. The env cause MUST be: an honest aify-env pairs
    `operator-stop` only with `stopped`, but the receiver validates state and cause independently, so a record that
    says it heard the stop while reporting another word is admitted. That is no unpublished stop; it stays unresolved.
    """
    return comms_word == 'stopped' and env.get('stateCause') != 'operator-stop'


def _background_shell_unpublished(comms, comms_word, env, env_word):
    """comms shows a background shell that aify-env has no input for yet (B4). Not a shell on a managed worker's
    screen: that is a different missing input (the screen), and stays unresolved until its own class exists."""
    return comms_word == 'shell' and comms.get('background_work') is True


def _turn_open_differs(comms, comms_word, env, env_word):
    """The two sides disagree on whether a turn is open. Telling timing from a fault needs turn.ageMs (B6)."""
    return env.get('turnOpen') is not None and comms.get('in_turn') is not env.get('turnOpen')


def _no_wake_handle(comms, comms_word, env, env_word):
    """A resident nothing can wake reads `offline` whatever aify-env sees of its process: the operator's Q3 ruling.
    `misconfigured` is the same fact as the authoritative producer words it."""
    return comms_word in ('offline', 'misconfigured') and comms.get('wake_handle_absent') is True


DISAGREEMENT_CLASSES = (
    DisagreementClass('no-wake-handle', 'policy:Q3', _no_wake_handle),
    DisagreementClass('comms-only-stop', 'B2', _comms_only_stop),
    DisagreementClass('background-shell-unpublished', 'B4', _background_shell_unpublished),
    DisagreementClass('turn-open-differs', 'B6', _turn_open_differs),
)


def classify(comms, comms_word, env, env_word):
    """The name of the one class this disagreement satisfies, or why it is unresolved."""
    if not env:
        return 'unresolved:unmatched'  # a cause read against facts nobody published is not an attribution
    matched = [c.name for c in DISAGREEMENT_CLASSES if c.applies(comms, comms_word, env, env_word)]
    if len(matched) == 1:
        return matched[0]
    return 'unresolved:unmatched' if not matched else 'unresolved:ambiguous'


def env_inputs(record):
    """The closed facts of one validated aify-env record a class may read. Never identities or free text."""
    turn = record['turn']
    return (('stateCause', record['stateCause']), ('busy', record['busy']),
            ('process', record['process']['state']), ('verified', record['process']['verified']),
            ('turnOpen', None if turn is None else turn['open']),
            ('awaitingInput', None if turn is None else turn['awaitingInput']))
