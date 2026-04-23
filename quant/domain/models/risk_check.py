from dataclasses import dataclass


@dataclass(frozen=True)
class RiskCheckResult:
    passed: bool
    is_hard_limit: bool
    check_name: str
    message: str
    current_value: float
    limit_value: float
