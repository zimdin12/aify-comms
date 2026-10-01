"""Make the four 12766276 review witnesses read 12766276's vocabulary, so they fail on the defect, not on
a field or column that tree never had. Only the worktree copy is edited."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "service/tests/test_definition_change_requests.py"
text = path.read_bytes().decode("utf-8")
edits = [
    # R1: 12766276 answers 200 with no `detail`; show the status it gave.
    ('skipped.json()["detail"]', 'skipped.json().get("detail")'),
    ('expired.json()["detail"]', 'expired.json().get("detail")'),
    # R2: no consequence field there; the whole-row comparison is the witness.
    ('        self.assertTrue(first.json()["request"]["consequence"].startswith("nothing removed: "), first.text)\n', ''),
    # Interruption: no consequence column there; status only, then the agent must be gone.
    ('"SELECT status, consequence FROM definition_requests WHERE id = ?"', '"SELECT status FROM definition_requests WHERE id = ?"'),
    ('        self.assertEqual(row, {"status": "done", "consequence": "pending"}, "the host\'s done is recorded, the removal owed")\n', ''),
    ('        self.assertEqual(finished.json()["request"]["consequence"], "removed", finished.text)\n', ''),
    # R3: 12766276 words the refusal with a "; nothing removed" suffix.
    ('(0, "the definition this removal was for ended another way"))', '(0, "the definition this removal was for ended another way; nothing removed"))'),
]
for old, new in edits:
    if text.count(old) != 1:
        sys.exit(f"edit anchor not found exactly once: {old!r}")
    text = text.replace(old, new)
path.write_bytes(text.encode("utf-8"))
print("back-ported", len(edits), "edits")
