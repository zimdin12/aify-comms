"""The receiver's delivery facts and decisions. No process, DB or transport effects."""
from dataclasses import dataclass
from typing import Callable

from service.api_core.capabilities import (
    _has_codex_live_app_server,
    _has_hermes_gateway_url,
    _managed_via_wrapper_for_runtime,
    _row_capabilities,
)
from service.api_core.runtime import _normalize_launch_mode, _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES


@dataclass(frozen=True)
class DeliveryInputs:
    runtime: str
    mode: str
    disabled: bool
    handle: str
    capabilities: list[str]
    config: dict

    @property
    def resident_run(self):
        return 'resident-run' in self.capabilities

    @property
    def app_server(self):
        return _has_codex_live_app_server(self.config)

    @property
    def gateway(self):
        return _has_hermes_gateway_url(self.config)

    @property
    def dispatch_gateway(self):
        # ASYMMETRY(hermes): historical refusal checks accept lower-case URL schemes only.
        return str(self.config.get('gatewayUrl') or '').strip().startswith(('ws://', 'wss://'))


Rule = tuple[Callable[[DeliveryInputs], bool], str]


@dataclass(frozen=True)
class DeliveryProfile:
    fact: str
    channel: str = ''
    channel_claim: bool = False
    wrapper_child: bool = False
    wake: tuple[Rule, ...] = ()
    refuse: tuple[Rule, ...] = ()
    steer_via_claim_modes: tuple[str, ...] = ()

    @property
    def sidecar(self):
        return bool(self.channel)

    def channel_enabled(self, config):
        return self.sidecar and isinstance(config, dict) and bool(config.get('channelEnabled'))


# The key is the registered runtime only for old rows and the runtime/profile agreement check.
# A delivery word describes transport, not permission to run. The run capabilities and concrete
# connection evidence still have to clear the ordered rules in each resident profile.
LEGACY_DELIVERY = {
    'claude-code': DeliveryProfile('wake:channel', channel='always', channel_claim=True, wake=(
        (lambda i: i.resident_run, 'claude-live'),
        (lambda i: True, 'claude-needs-channel'),
    ), refuse=(
        (lambda i: not i.resident_run and not i.config.get('channelEnabled'),
         'agent "{id}" is a resident Claude session without channelEnabled. '
         'Restart with `claude-aify` (which sets AIFY_CHANNELS_ENABLED=1) and re-register from that session.'),
    )),
    'codex': DeliveryProfile('wake:thread', channel_claim=True, wrapper_child=True, wake=(
        (lambda i: i.resident_run and i.handle and i.app_server, 'codex-live'),
        (lambda i: i.resident_run and i.handle, 'codex-thread-resume'),
        (lambda i: not i.handle, 'codex-missing-handle'),
    ), refuse=(
        (lambda i: not i.resident_run and not i.app_server,
         'agent "{id}" is a resident Codex session without a live appServerUrl. '
         'Restart with `codex-aify` and re-register passing `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` and `sessionHandle="$CODEX_THREAD_ID"`.'),
        (lambda i: i.resident_run and not i.handle,
         'agent "{id}" is a resident Codex session without a bound session handle. '
         'Re-register that live session or provide sessionHandle explicitly.'),
    )),
    # ASYMMETRY(hermes): the gateway sidecar claims a run and calls native session.steer;
    # it does not consume dispatch_controls for channel or resident runs.
    'hermes': DeliveryProfile('wake:gateway', channel='flag', channel_claim=True,
                              steer_via_claim_modes=('channel', 'resident'), wake=(
        (lambda i: i.resident_run and i.gateway, 'hermes-live'),
        (lambda i: not i.gateway, 'hermes-missing-handle'),
    ), refuse=(
        (lambda i: not i.resident_run and not i.dispatch_gateway,
         'agent "{id}" is a resident Hermes session without a live gatewayUrl. '
         'Restart with the updated `hermes-aify` (which exports AIFY_HERMES_GATEWAY_URL) and re-register — the bridge auto-detects the gateway from env. '
         'Verify the wrapper is current with `head -30 ~/.local/bin/hermes-aify | grep pick_port` (function exists in the new wrapper).'),
        (lambda i: i.resident_run and not i.handle and not i.dispatch_gateway,
         'agent "{id}" is a resident Hermes session without a bound session handle. '
         'Restart with hermes-aify and a resumable session handle, or create an environment-managed session.'),
    )),
    'pi': DeliveryProfile('wake:pi-presence', wake=((lambda i: True, 'presence-only'),), refuse=(
        (lambda i: True, 'agent "{id}" is a Pi/OMP presence session, not a triggerable resident target. '
         'Switch to managed or spawn a managed Pi agent so delivery uses the bridge-owned OMP RPC worker.'),
    )),
    'opencode': DeliveryProfile('wake:opencode-presence', wake=((lambda i: True, 'presence-only'),), refuse=(
        (lambda i: True, 'agent "{id}" is an OpenCode presence session, not a triggerable resident target. '
         'Create an environment-managed OpenCode agent; resident OpenCode delivery is disabled until a real multi-client surface is wired.'),
    )),
}
_DEFAULT = DeliveryProfile('')
_FACTS = {profile.fact: runtime for runtime, profile in LEGACY_DELIVERY.items()}


def delivery_profile(runtime, capabilities=()):
    # A supplied word cannot widen the delivery paths of its registered runtime.
    reported = next((cap for cap in capabilities if _FACTS.get(cap) == runtime), None)
    return LEGACY_DELIVERY.get(_FACTS[reported] if reported else runtime, _DEFAULT)


def _inputs(row):
    raw = _json_loads_or(row.get('runtime_config') if isinstance(row, dict) else
                        row['runtime_config'] if 'runtime_config' in row.keys() else None, {})
    return DeliveryInputs(_normalize_runtime(row['runtime'] or 'generic'),
                          _normalize_session_mode(row['session_mode'] or 'resident'),
                          _normalize_launch_mode(row['launch_mode']) == 'none',
                          str(row['session_handle'] or '').strip(), _row_capabilities(row),
                          raw if isinstance(raw, dict) else {})


def wake_mode(row):
    inputs = _inputs(row)
    if inputs.disabled:
        return 'disabled'
    if inputs.mode == 'managed' and 'managed-run' in inputs.capabilities:
        return 'managed-worker'
    if inputs.mode == 'resident':
        profile = delivery_profile(inputs.runtime, inputs.capabilities)
        return next((word for matches, word in profile.wake if matches(inputs)), 'message-only')
    return 'message-only'


def execution_mode(row, requested_runtime=None, settings=None):
    runtime = _normalize_runtime(row['runtime'] or 'generic')
    if requested_runtime and _normalize_runtime(requested_runtime) != runtime:
        return None, f'requested runtime "{requested_runtime}" does not match registered runtime "{runtime}"'
    if runtime not in LAUNCHABLE_RUNTIMES:
        return None, f'runtime "{runtime}" does not support active dispatch'
    inputs = _inputs(row)
    profile = delivery_profile(runtime, inputs.capabilities)
    if inputs.mode == 'managed':
        if inputs.disabled:
            return None, 'launch mode is disabled'
        if settings is not None and _managed_via_wrapper_for_runtime(settings, runtime):
            return 'channel', None
        enabled = profile.channel_enabled(inputs.config)
        if inputs.capabilities and 'managed-run' not in inputs.capabilities and not enabled:
            return None, 'agent capabilities do not include "managed-run"'
        return ('channel' if profile.channel == 'always' or enabled else 'managed'), None
    reason = next((text for matches, text in profile.refuse if matches(inputs)), '')
    if reason:
        return None, reason.format(id=row['id'])
    if not inputs.resident_run:
        return None, 'agent capabilities do not include "resident-run" — re-register from a live aify-wrapper session with the runtime\'s wake handle.'
    if inputs.disabled:
        return None, 'launch mode is disabled'
    return 'resident', None
