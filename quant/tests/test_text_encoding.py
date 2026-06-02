"""Repository text encoding guardrails."""
from pathlib import Path

from scripts.check_text_encoding import check_files, iter_text_files


ROOT = Path(__file__).resolve().parents[2]


def test_editorconfig_enforces_utf8():
    content = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    assert "charset = utf-8" in content


def test_check_text_encoding_flags_invalid_utf8():
    bad_file = ROOT / "tmp" / "bad_encoding_fixture.md"
    bad_file.write_bytes(b"\xff")
    try:
        errors = check_files([bad_file])
        assert errors
        assert "invalid UTF-8" in errors[0]
    finally:
        bad_file.unlink(missing_ok=True)


def test_check_text_encoding_skips_qmt_virtualenv(tmp_path):
    qmt_file = tmp_path / ".venv-qmt" / "Lib" / "site-packages" / "xtquant" / "config" / "tradeTime.txt"
    qmt_file.parent.mkdir(parents=True)
    qmt_file.write_bytes(b"\xff")

    assert list(iter_text_files(tmp_path)) == []
