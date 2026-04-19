import uuid
import sys
import importlib.util
from pathlib import Path
from typing import Optional

from quant.research.models import ScoredIdea, StrategyCandidate
from quant.research.templates.prompts.coder_prompt import CODER_SYSTEM_PROMPT, CODER_USER_PROMPT


class CoderAgent:
    def __init__(self, llm_adapter, strategies_dir: Optional[Path] = None, max_retries: int = 2):
        self.llm = llm_adapter
        self.strategies_dir = strategies_dir or (Path(__file__).parent.parent.parent / "strategies")
        self.max_retries = max_retries

    def _make_strategy_dir(self, name: str) -> Path:
        safe_name = name.lower().replace(" ", "_").replace("-", "_")[:30]
        dir_path = self.strategies_dir / safe_name
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def _write_files(self, dir_path: Path, name: str, code: str, config_yaml: str) -> tuple:
        strategy_file = dir_path / "strategy.py"
        config_file = dir_path / "config.yaml"
        strategy_file.write_text(code, encoding="utf-8")
        config_file.write_text(config_yaml, encoding="utf-8")
        return str(strategy_file), str(config_file)

    def _validate_code(self, code: str) -> bool:
        try:
            compile(code, "<string>", "exec")
            return True
        except SyntaxError:
            return False

    def _register_strategy(self, strategy_file: Path) -> bool:
        try:
            module_name = f"quant.strategies.{strategy_file.parent.name}.strategy"
            spec = importlib.util.spec_from_file_location(module_name, strategy_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                for attr_name in dir(module):
                    cls = getattr(module, attr_name)
                    if hasattr(cls, "_registry_name"):
                        from quant.strategies.registry import StrategyRegistry
                        if StrategyRegistry.is_registered(cls._registry_name):
                            return True
        except Exception:
            pass
        return False

    async def implement(self, idea: ScoredIdea) -> StrategyCandidate:
        safe_name = idea.raw_idea.title[:30].replace(" ", "").replace("-", "_")
        strategy_name = f"Auto{safe_name}"

        user_prompt = CODER_USER_PROMPT.format(
            title=idea.raw_idea.title,
            implementation_plan=idea.implementation_plan,
            factors=", ".join(idea.suggested_factors),
            params=str(idea.suggested_params),
        )

        code = None
        config_yaml = None

        for attempt in range(self.max_retries):
            try:
                result = self.llm.analyze(
                    prompt=user_prompt,
                    context={"system_prompt": CODER_SYSTEM_PROMPT}
                )

                if isinstance(result, dict):
                    code = result.get("code", "")
                    config_yaml = result.get("config_yaml", "")
                else:
                    code = ""
                    config_yaml = ""

                if code and self._validate_code(code):
                    break
            except Exception:
                pass

        strategy_dir = self._make_strategy_dir(safe_name)

        if not code or not self._validate_code(code):
            code_path, config_path = self._write_files(
                strategy_dir, strategy_name,
                f"# Code generation failed\n",
                f"name: {strategy_name}\nparameters: {{}}\n",
            )
            return StrategyCandidate(
                id=str(uuid.uuid4()),
                scored_idea=idea,
                strategy_name=strategy_name,
                code_path=code_path,
                config_path=config_path,
                registered=False,
            )

        code_path, config_path = self._write_files(strategy_dir, strategy_name, code, config_yaml)
        registered = self._register_strategy(Path(code_path))

        return StrategyCandidate(
            id=str(uuid.uuid4()),
            scored_idea=idea,
            strategy_name=strategy_name,
            code_path=code_path,
            config_path=config_path,
            registered=registered,
        )
