import hashlib
import json
import subprocess
from typing import Dict


class RunRecorder:
    @staticmethod
    def hash_config(config: Dict) -> str:
        serialized = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    @staticmethod
    def hash_data(data_summary: Dict) -> str:
        serialized = json.dumps(data_summary, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    @staticmethod
    def get_code_version() -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return "unknown"
