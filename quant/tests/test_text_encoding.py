"""Repository text encoding guardrails."""
from pathlib import Path

from scripts.check_text_encoding import check_files


ROOT = Path(__file__).resolve().parents[2]


def test_editorconfig_enforces_utf8():
    content = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    assert "charset = utf-8" in content


def test_check_text_encoding_flags_invalid_utf8(tmp_path):
    bad_file = tmp_path / "bad.md"
    bad_file.write_bytes(b"\xff")
    errors = check_files([bad_file])
    assert errors
    assert "invalid UTF-8" in errors[0]
