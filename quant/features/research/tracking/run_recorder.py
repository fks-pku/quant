import hashlib
import json
import subprocess
from typing import Any, Dict


class RunRecorder:
    @staticmethod
    def hash_config(config: Dict[str, Any]) -> str:
        return RunRecorder._hash_mapping(config)

    @staticmethod
    def hash_data(data_summary: Dict[str, Any]) -> str:
        return RunRecorder._hash_mapping(data_summary)

    @staticmethod
    def get_code_version() -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            value = result.stdout.strip()
            return value or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _hash_mapping(data: Dict[str, Any]) -> str:
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
