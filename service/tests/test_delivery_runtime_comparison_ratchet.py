"""Track the issue #35 scan; expansion of the delivery refactor is a separate decision."""
import re
from pathlib import Path


SERVICE = Path(__file__).resolve().parents[1]
COMPARISON = re.compile(r'runtime\w*\s*(==|!=)\s*"(claude-code|codex|hermes|pi|opencode)"')
DELIVERY = {'liveness.py', 'execution_mode.py', 'channel_delivery.py', 'delivery_policy.py'}
MAX_MATCHES = 14


def test_delivery_runtime_comparisons_do_not_grow():
    matches = []
    untagged = []
    for source in SERVICE.rglob('*.py'):
        relative = source.relative_to(SERVICE)
        if 'tests' in relative.parts:
            continue
        for number, line in enumerate(source.read_text(encoding='utf-8').splitlines(), 1):
            if COMPARISON.search(line):
                location = f'{relative.as_posix()}:{number}'
                matches.append(location)
                if relative.parent.as_posix() == 'api_core' and relative.name in DELIVERY and 'ASYMMETRY(' not in line:
                    untagged.append(location)
    assert not untagged, f'untagged delivery runtime comparisons: {untagged}'
    assert len(matches) <= MAX_MATCHES, f'runtime comparison budget {MAX_MATCHES} exceeded: {matches}'
