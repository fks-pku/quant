from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from quant.features.research.models import RawStrategy


WORLDQUANT_101_SOURCE_URL = "https://arxiv.org/abs/1601.00991"
DOLPHINDB_WQ101_DOC_URL = "https://docs.dolphindb.com/en/3.00.5/Tutorials/wq101alpha.html"
WORLDQUANT_101_AUTHORS = "Zura Kakushadze"
WORLDQUANT_101_PUBLISHED_DATE = "2016-01-05"

LOCAL_DAILY_CN_FIELDS = {"open", "high", "low", "close", "volume"}
PARAM_TO_LOCAL_FIELD = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
    "volume": "volume",
    "vwap": "vwap",
    "cap": "cap",
    "indclass": "indclass",
}


def build_worldquant101_raw_strategies(
    alpha_numbers: Optional[Sequence[int]] = None,
    ready_only: bool = False,
) -> List[RawStrategy]:
    allowed = {int(value) for value in alpha_numbers} if alpha_numbers else set(range(1, 102))
    rows: List[RawStrategy] = []
    for alpha_number in range(1, 102):
        if alpha_number not in allowed:
            continue
        spec = worldquant101_alpha_spec(alpha_number)
        if ready_only and not spec["a_share_ready"]:
            continue
        rows.append(_raw_strategy(spec))
    return rows


def worldquant101_alpha_spec(alpha_number: int) -> Dict[str, Any]:
    if alpha_number not in ALPHA_REQUIRED_PARAMS:
        raise ValueError(f"Unknown WorldQuant 101 alpha number: {alpha_number}")
    source_params = ALPHA_REQUIRED_PARAMS[alpha_number]
    local_fields = tuple(PARAM_TO_LOCAL_FIELD[param] for param in source_params)
    required_local_fields = tuple(dict.fromkeys(local_fields))
    missing = tuple(field for field in required_local_fields if field not in LOCAL_DAILY_CN_FIELDS)
    families = _factor_families(source_params)
    return {
        "alpha_number": int(alpha_number),
        "alpha_id": f"wq_alpha_{alpha_number:03d}",
        "title": f"WorldQuant 101 Alpha #{alpha_number:03d}",
        "source_parameters": source_params,
        "required_local_fields": required_local_fields,
        "missing_daily_cn_fields": missing,
        "a_share_ready": not missing,
        "families": families,
        "requires_industry": "indclass" in source_params,
        "requires_cap": "cap" in source_params,
        "requires_vwap": "vwap" in source_params,
    }


class WorldQuant101Source:
    def search(self, query: Optional[Dict[str, Any]] = None, max_results: int = 101) -> List[Dict[str, Any]]:
        cfg = query or {}
        alpha_numbers = _alpha_numbers_from_query(cfg)
        ready_only = bool(cfg.get("ready_only", False))
        rows = build_worldquant101_raw_strategies(alpha_numbers=alpha_numbers, ready_only=ready_only)
        limit = max(0, int(max_results or 0))
        selected = rows[:limit] if limit else []
        return [_raw_to_dict(row) for row in selected]


def _raw_strategy(spec: Dict[str, Any]) -> RawStrategy:
    required = ", ".join(spec["required_local_fields"])
    families = ", ".join(spec["families"])
    if spec["a_share_ready"]:
        coverage = "current A-share daily_cn data-ready using local OHLCV fields"
        validation_note = "ready for exact-expression implementation and full A-share rank IC validation"
    else:
        missing = ", ".join(spec["missing_daily_cn_fields"])
        coverage = f"requires additional fields or proxies before A-share validation: {missing}"
        validation_note = "hold as a factor-library seed until the missing data fields are mapped"
    description = (
        f"WorldQuant Alpha #{spec['alpha_number']:03d} from the public 101 Formulaic Alphas library. "
        f"It is a short-horizon cross-sectional formulaic equity alpha requiring {required}. "
        f"Factor family tags: {families}. Current local status: {coverage}. "
        f"Use it as an idea-bank factor seed first, then {validation_note}; do not treat it as a "
        "validated A-share strategy without local HFQ daily-bar validation, turnover, cost, and benchmark checks."
    )
    return RawStrategy(
        title=spec["title"],
        description=description,
        source="worldquant101",
        source_url=WORLDQUANT_101_SOURCE_URL,
        authors=WORLDQUANT_101_AUTHORS,
        published_date=WORLDQUANT_101_PUBLISHED_DATE,
        metadata={
            "external_library": "worldquant_101_formulaic_alphas",
            "alpha_number": spec["alpha_number"],
            "alpha_id": spec["alpha_id"],
            "source_parameters": list(spec["source_parameters"]),
            "required_local_fields": list(spec["required_local_fields"]),
            "missing_daily_cn_fields": list(spec["missing_daily_cn_fields"]),
            "a_share_ready": bool(spec["a_share_ready"]),
            "factor_families": list(spec["families"]),
            "formula_source": WORLDQUANT_101_SOURCE_URL,
            "parameter_source": DOLPHINDB_WQ101_DOC_URL,
            "formula_text_included": False,
            "license_note": (
                "Public idea metadata only; exact formula expressions should be implemented from an allowed source "
                "and independently validated on local A-share data."
            ),
        },
    )


def _raw_to_dict(raw: RawStrategy) -> Dict[str, Any]:
    return {
        "title": raw.title,
        "description": raw.description,
        "source": raw.source,
        "source_url": raw.source_url,
        "authors": raw.authors,
        "published_date": raw.published_date,
        "metadata": dict(raw.metadata or {}),
    }


def _alpha_numbers_from_query(query: Dict[str, Any]) -> Optional[List[int]]:
    value = query.get("alpha_numbers") or query.get("alphas") or query.get("alpha")
    if value is None:
        return None
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        return sorted(_parse_alpha_numbers(value))
    numbers = []
    for item in value:
        if isinstance(item, str) and "-" in item:
            numbers.extend(sorted(_parse_alpha_numbers(item)))
        else:
            numbers.append(int(item))
    return sorted({number for number in numbers if 1 <= number <= 101})


def _parse_alpha_numbers(text: str) -> Set[int]:
    numbers: Set[int] = set()
    for chunk in text.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            numbers.update(range(int(start), int(end) + 1))
        else:
            numbers.add(int(chunk))
    return {number for number in numbers if 1 <= number <= 101}


def _factor_families(params: Tuple[str, ...]) -> List[str]:
    families = []
    param_set = set(params)
    if {"open", "close"} & param_set:
        families.append("price_action")
    if {"high", "low"} & param_set:
        families.append("range_volatility")
    if "vol" in param_set:
        families.append("volume_liquidity")
    if "vwap" in param_set:
        families.append("vwap_microstructure_proxy")
    if {"cap", "indclass"} & param_set:
        families.append("cross_sectional_neutralization")
    return families or ["price_action"]


def _register(target: Dict[int, Tuple[str, ...]], alpha_numbers: str, params: str) -> None:
    parsed_params = tuple(item.strip() for item in params.split(",") if item.strip())
    for alpha_number in _parse_alpha_numbers(alpha_numbers):
        target[alpha_number] = parsed_params


def _build_required_params() -> Dict[int, Tuple[str, ...]]:
    params: Dict[int, Tuple[str, ...]] = {}
    _register(params, "1,9,10,19,24,29,34,46,49,51", "close")
    _register(params, "23", "high")
    _register(params, "71", "vwap,vol,open,close,low")
    _register(params, "2,14", "vol,open,close")
    _register(params, "25,47,74", "vwap,vol,close,high")
    _register(params, "72,77", "vwap,vol,high,low")
    _register(params, "3,6", "vol,open")
    _register(params, "27,50,61,81", "vwap,vol")
    _register(params, "73", "vwap,open,low")
    _register(params, "4", "low")
    _register(params, "28,35,55,60,68,85", "vol,high,low,close")
    _register(params, "75,78", "vwap,vol,low")
    _register(params, "5", "vwap,open,close")
    _register(params, "31,52", "vol,close,low")
    _register(params, "83", "vwap,vol,close,high,low")
    _register(params, "7,12,13,17,21,30,39,43,45", "vol,close")
    _register(params, "32,42,57,84", "vwap,close")
    _register(params, "88,92,94", "vol,open,close,high,low")
    _register(params, "8,18,33,37,38", "open,close")
    _register(params, "36,86", "vwap,vol,open,close")
    _register(params, "95", "vol,open,high,low")
    _register(params, "11,96", "vwap,vol,close")
    _register(params, "41", "vwap,high,low")
    _register(params, "65,98", "vwap,vol,open")
    _register(params, "15,16,26,40,44", "vol,high")
    _register(params, "53", "close,high,low")
    _register(params, "99", "vol,high,low")
    _register(params, "20,54,101", "open,close,high,low")
    _register(params, "62,64", "vwap,vol,open,high,low")
    _register(params, "22", "vol,high,close")
    _register(params, "66", "vwap,open,high,low")
    _register(params, "48", "close,indclass")
    _register(params, "76,89", "vwap,vol,low,indclass")
    _register(params, "56", "close,cap")
    _register(params, "80", "vol,open,high,indclass")
    _register(params, "58,59", "vwap,vol,indclass")
    _register(params, "82", "vol,open,indclass")
    _register(params, "63,79", "vwap,vol,open,close,indclass")
    _register(params, "90", "vol,close,indclass")
    _register(params, "67", "vwap,vol,high,indclass")
    _register(params, "97", "vwap,vol,low,indclass")
    _register(params, "69,70,87,91,93", "vwap,vol,close,indclass")
    _register(params, "100", "vol,close,high,low,indclass")
    missing = sorted(set(range(1, 102)) - set(params))
    if missing:
        raise RuntimeError(f"WorldQuant 101 parameter map is incomplete: {missing}")
    return params


ALPHA_REQUIRED_PARAMS = _build_required_params()

