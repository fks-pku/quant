"""YAML configuration loader."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class ConfigLoader:
    """Loads and manages YAML configuration files."""

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir is None:
            self.config_dir = Path(__file__).parent.parent / "config"
        else:
            self.config_dir = Path(config_dir)
        self._configs: Dict[str, Any] = {}

    def load(self, filename: str, key: Optional[str] = None) -> Dict[str, Any]:
        """Load a YAML configuration file."""
        if key and key in self._configs:
            return self._configs[key]

        path = self.config_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r") as f:
            config = yaml.safe_load(f)

        if key:
            self._configs[key] = config
        return config

    def get(self, filename: str, *keys: str, default: Any = None) -> Any:
        """Get a nested config value using dot notation keys."""
        config = self.load(filename)
        for k in keys:
            if isinstance(config, dict) and k in config:
                config = config[k]
            else:
                return default
        return config

    def reload(self, filename: str) -> Dict[str, Any]:
        """Reload a configuration file."""
        path = self.config_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r") as f:
            config = yaml.safe_load(f)

        self._configs[filename] = config
        return config