"""Check repository text files decode as UTF-8."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Iterator, List


TEXT_EXTENSIONS = {
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-qmt",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


def iter_text_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
            yield path


def check_files(paths: Iterable[Path]) -> List[str]:
    errors: List[str] = []
    for path in paths:
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"{path}: invalid UTF-8 at byte {exc.start}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[Path.cwd()])
    args = parser.parse_args(argv)

    files: List[Path] = []
    for path in args.paths:
        if path.is_dir():
            files.extend(iter_text_files(path))
        elif path.is_file():
            files.append(path)

    errors = check_files(files)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"Checked {len(files)} text files as UTF-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
