"""Track executable issue #35 comparisons, independent of prose and quote style."""
import ast
import io
import re
import tokenize
from pathlib import Path

from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES


SERVICE = Path(__file__).resolve().parents[1]
RUNTIME_NAME = re.compile(r'runtime\w*$')
DELIVERY = {'liveness.py', 'execution_mode.py', 'channel_delivery.py', 'delivery_policy.py'}
MAX_MATCHES = 8


def _runtime_comparison_lines(source: str):
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        for left, op, right in zip((node.left, *node.comparators), node.ops, node.comparators):
            name = left.id if isinstance(left, ast.Name) else left.attr if isinstance(left, ast.Attribute) else ''
            if (RUNTIME_NAME.search(name) and isinstance(op, (ast.Eq, ast.NotEq))
                    and isinstance(right, ast.Constant) and isinstance(right.value, str)
                    and right.value in LAUNCHABLE_RUNTIMES):
                yield left.lineno


def _asymmetry_lines(source: str) -> set[int]:
    return {token.start[0] for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type == tokenize.COMMENT and 'ASYMMETRY(' in token.string}


def test_only_code_comparisons_count():
    source = """'''A docstring shows runtime == "pi".'''
# runtime == "pi"
if runtime == "pi":
    pass
if normalized_runtime != 'codex':
    pass
"""
    assert list(_runtime_comparison_lines(source)) == [3, 5]


def test_delivery_runtime_comparisons_do_not_grow():
    matches = []
    untagged = []
    for source in SERVICE.rglob('*.py'):
        relative = source.relative_to(SERVICE)
        if 'tests' in relative.parts:
            continue
        text = source.read_text(encoding='utf-8')
        in_delivery = relative.parent.as_posix() == 'api_core' and relative.name in DELIVERY
        tagged = _asymmetry_lines(text) if in_delivery else set()
        for number in _runtime_comparison_lines(text):
            location = f'{relative.as_posix()}:{number}'
            matches.append(location)
            if in_delivery and number not in tagged:
                untagged.append(location)
    assert not untagged, f'untagged delivery runtime comparisons: {untagged}'
    assert len(matches) <= MAX_MATCHES, f'runtime comparison budget {MAX_MATCHES} exceeded: {matches}'
