import hashlib
import re


def normalize_strategy_text(title: str, description: str = "") -> str:
    normalized_title = re.sub(r"\s+", " ", (title or "").lower().strip())
    normalized_description = re.sub(r"\s+", " ", (description or "").lower().strip())[:200]
    return f"{normalized_title}::{normalized_description}"


def hash_strategy_text(title: str, description: str = "") -> str:
    return hashlib.md5(normalize_strategy_text(title, description).encode()).hexdigest()
