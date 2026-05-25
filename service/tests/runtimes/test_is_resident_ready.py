"""Per-adapter is_resident_ready assertions. Closes #120 — claude resident
requires runtime_config.channelEnabled=True before advertising resident-run.
"""


def test_claude_is_resident_ready_requires_channel_enabled():
    from service.runtimes.claude import ClaudeAdapter
    a = ClaudeAdapter()
    assert a.is_resident_ready({"channelEnabled": True}) is True
    assert a.is_resident_ready({"channelEnabled": False}) is False
    assert a.is_resident_ready({"channelEnabled": "true"}) is False  # strict identity check
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready(None) is False


def test_codex_is_resident_ready_always_true():
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    assert a.is_resident_ready({}) is True
    assert a.is_resident_ready({"anything": "else"}) is True
    assert a.is_resident_ready(None) is True


def test_hermes_is_resident_ready_requires_valid_gateway_url():
    from service.runtimes.hermes import HermesAdapter
    a = HermesAdapter()
    assert a.is_resident_ready({"gatewayUrl": "ws://127.0.0.1:9999/api/ws"}) is True
    assert a.is_resident_ready({"gatewayUrl": "wss://example.com/api/ws?token=x"}) is True
    assert a.is_resident_ready({"gatewayUrl": "http://nope"}) is False
    assert a.is_resident_ready({"gatewayUrl": "${AIFY_HERMES_GATEWAY_URL}"}) is False
    assert a.is_resident_ready({"gatewayUrl": ""}) is False
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready(None) is False


def test_pi_is_resident_ready_always_false():
    from service.runtimes.pi import PiAdapter
    a = PiAdapter()
    assert a.is_resident_ready({}) is False
    assert a.is_resident_ready({"channelEnabled": True}) is False


def test_opencode_is_resident_ready_always_false():
    from service.runtimes.opencode import OpencodeAdapter
    a = OpencodeAdapter()
    assert a.is_resident_ready({}) is False
