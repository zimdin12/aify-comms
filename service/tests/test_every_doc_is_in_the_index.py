"""Direct Markdown children are indexed in the live docs or finished-work archive."""
from pathlib import Path
import re

DOCS = Path(__file__).resolve().parents[2] / 'docs'
ARCHIVE = DOCS / 'archive'


def _missing_documents(directory: Path) -> list[str]:
    index = (directory / 'README.md').read_text(encoding='utf-8')
    # Match a whole backticked name, with or without the extension; FooBar cannot document Foo.
    named = set(re.findall(r'`([^`\n]+)`', index))
    return sorted(path.name for path in directory.glob('*.md')
                  if path.name != 'README.md' and path.name not in named and path.stem not in named)


def test_both_indexes_and_their_real_documents_are_reachable():
    live = DOCS / 'README.md'
    archive_index = ARCHIVE / 'README.md'
    assert (DOCS / 'ARCHITECTURE.md').is_file()
    assert (ARCHIVE / 'V0.2_PLAN.md').is_file()
    live_text = live.read_text(encoding='utf-8')
    archive_text = archive_index.read_text(encoding='utf-8')
    assert '](ARCHITECTURE.md)' in live_text
    assert '](archive/README.md)' in live_text
    assert '](V0.2_PLAN.md)' in archive_text
    assert '](../README.md)' in archive_text


def test_the_check_detects_an_unindexed_file(tmp_path):
    (tmp_path / 'README.md').write_text('`Known` and `Unindexed-suffix.md`', encoding='utf-8')
    (tmp_path / 'Known.md').touch()
    (tmp_path / 'Unindexed.md').touch()
    assert _missing_documents(tmp_path) == ['Unindexed.md']


def test_every_document_is_named_in_its_own_index():
    assert _missing_documents(DOCS) == [], 'name live documents in docs/README.md'
    assert _missing_documents(ARCHIVE) == [], 'name finished work in docs/archive/README.md'
