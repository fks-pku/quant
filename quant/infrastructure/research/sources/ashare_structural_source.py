from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

ASHARE_STRUCTURAL_SOURCE = "ashare_structural"
ASHARE_STRUCTURAL_SOURCE_URL = "local://ashare_structural_daily_ideas"
ASHARE_STRUCTURAL_AUTHORS = "Codex Quant Research"
ASHARE_STRUCTURAL_PUBLISHED_DATE = "2026-05-17"


_IDEAS: tuple[Dict[str, Any], ...] = (
    {
        "idea_id": "ashare_short_reversal_5d",
        "title": "A-Share Short-Term Reversal 5D",
        "formula_key": "ashare_short_reversal_5d",
        "strategy_type": "mean_reversion",
        "required_fields": ["close"],
        "lookback_days": 5,
        "horizon_days": 5,
        "family": "residual_reversal",
        "description": (
            "A-share daily OHLCV structural idea: rank stocks by negative trailing 5-day HFQ adjusted close return "
            "and buy the strongest short-term losers. Economic thesis: retail overreaction, T+1 constraints, and "
            "limited arbitrage can create short-horizon reversal, but the signal must survive transaction costs, "
            "turnover controls, daily price limits, and 000300 excess-return checks."
        ),
    },
    {
        "idea_id": "ashare_volume_exhaustion_reversal",
        "title": "A-Share Volume Exhaustion Reversal",
        "formula_key": "ashare_volume_exhaustion_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["close", "volume"],
        "lookback_days": 20,
        "horizon_days": 5,
        "family": "trading_friction",
        "description": (
            "A-share daily OHLCV structural idea: buy recent 5-day losers only when volume is abnormally high versus "
            "its trailing 20-day average. Economic thesis: capitulation volume among retail-heavy A-shares can mark "
            "temporary liquidity imbalance; the gate should reject it unless Top 20 after-cost Sharpe, drawdown, and "
            "benchmark excess remain acceptable."
        ),
    },
    {
        "idea_id": "ashare_volume_dryup_pullback",
        "title": "A-Share Volume Dry-Up Pullback Reversal",
        "formula_key": "ashare_volume_dryup_pullback",
        "strategy_type": "mean_reversion",
        "required_fields": ["close", "volume"],
        "lookback_days": 20,
        "horizon_days": 5,
        "family": "trading_friction",
        "description": (
            "A-share daily OHLCV structural idea: buy negative 5-day pullbacks where trading volume has dried up below "
            "its trailing average. Economic thesis: low-volume pullbacks can reflect weak selling pressure rather than "
            "fundamental news, but only low-turnover long-only implementations should be promoted."
        ),
    },
    {
        "idea_id": "ashare_lottery_demand_avoidance",
        "title": "A-Share Lottery Demand Avoidance",
        "formula_key": "ashare_lottery_demand_avoidance",
        "strategy_type": "factor",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 10,
        "family": "lottery_preference",
        "description": (
            "A-share daily OHLCV structural idea: prefer stocks with low trailing maximum daily return and low realized "
            "volatility, avoiding lottery-like names sought by speculative retail flow. Economic thesis: short-sale "
            "constraints and retail lottery demand can overprice high-skewness stocks; the deployable version is a "
            "long-only low-lottery basket benchmarked against 000300."
        ),
    },
    {
        "idea_id": "ashare_low_volatility_defensive",
        "title": "A-Share Low Volatility Defensive Factor",
        "formula_key": "ashare_low_volatility_defensive",
        "strategy_type": "factor",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 10,
        "family": "risk_anomaly",
        "description": (
            "A-share daily OHLCV structural idea: rank stocks by low trailing 20-day realized volatility. Economic "
            "thesis: leverage constraints, lottery preference, and retail speculation can leave low-risk stocks with "
            "better risk-adjusted forward returns; strict validation must check benchmark-relative drawdown and capacity."
        ),
    },
    {
        "idea_id": "ashare_gap_down_reversal",
        "title": "A-Share Gap-Down Reversal",
        "formula_key": "ashare_gap_down_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["open", "close"],
        "lookback_days": 2,
        "horizon_days": 3,
        "family": "price_limit_and_opening_auction",
        "description": (
            "A-share daily OHLCV structural idea: buy stocks with negative open-to-previous-close gaps using only daily "
            "open and HFQ adjusted close information. Economic thesis: daily limits and opening auction pressure can "
            "temporarily over-discount bad news; the idea is fragile and must be rejected if turnover or drawdown is high."
        ),
    },
    {
        "idea_id": "ashare_volatility_scaled_reversal",
        "title": "A-Share Volatility-Scaled Reversal",
        "formula_key": "ashare_volatility_scaled_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "family": "risk_controlled_reversal",
        "description": (
            "A-share daily OHLCV structural idea: buy recent 5-day losers only when the loss is large relative to "
            "trailing 20-day realized volatility. Economic thesis: volatility scaling avoids lottery-like crash names "
            "and turns short-term reversal into a more liquid, transaction-cost-aware rank IC candidate for Top 20 "
            "long-only validation against 000300."
        ),
    },
    {
        "idea_id": "ashare_liquidity_weighted_low_volatility",
        "title": "A-Share Liquidity-Weighted Low Volatility",
        "formula_key": "ashare_liquidity_weighted_low_volatility",
        "strategy_type": "factor",
        "required_fields": ["close", "turnover"],
        "lookback_days": 20,
        "horizon_days": 10,
        "family": "liquidity_quality",
        "description": (
            "A-share daily OHLCV factor: prefer low realized volatility stocks with high average daily turnover. "
            "Economic thesis: low-risk anomaly is more deployable when filtered by liquidity and capacity; validation "
            "must show after-cost Sharpe, low drawdown, and positive 000300 excess return."
        ),
    },
    {
        "idea_id": "ashare_low_volatility_momentum",
        "title": "A-Share Low Volatility Momentum",
        "formula_key": "ashare_low_volatility_momentum",
        "strategy_type": "momentum",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 10,
        "family": "risk_adjusted_momentum",
        "description": (
            "A-share daily OHLCV momentum idea: rank positive 20-day momentum by return per unit of realized volatility. "
            "Economic thesis: trend continuation is more tradable when lottery volatility is penalized; the long-only "
            "basket should be judged after transaction costs, turnover, and rolling OOS stability."
        ),
    },
    {
        "idea_id": "ashare_range_contraction_breakout",
        "title": "A-Share Range Contraction Breakout",
        "formula_key": "ashare_range_contraction_breakout",
        "strategy_type": "breakout",
        "required_fields": ["high", "low", "close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "family": "low_vol_breakout",
        "description": (
            "A-share daily OHLCV breakout idea: prefer stocks closing near their trailing 20-day high while daily "
            "high-low range has contracted. Economic thesis: quiet accumulation before a breakout may create a lower "
            "turnover momentum entry; it must survive full-universe rank IC, costs, and 000300 excess-return gates."
        ),
    },
    {
        "idea_id": "ashare_gap_down_liquid_reversal",
        "title": "A-Share Liquid Gap-Down Reversal",
        "formula_key": "ashare_gap_down_liquid_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["open", "close", "turnover"],
        "lookback_days": 20,
        "horizon_days": 3,
        "family": "auction_liquidity_reversal",
        "description": (
            "A-share daily OHLCV structural idea: buy negative open-to-previous-close gaps only in high-turnover names. "
            "Economic thesis: opening auction pressure and daily price limits can over-discount liquid stocks, while "
            "the liquidity filter keeps transaction costs and capacity under control."
        ),
    },
    {
        "idea_id": "ashare_turnover_stability_factor",
        "title": "A-Share Turnover Stability Factor",
        "formula_key": "ashare_turnover_stability_factor",
        "strategy_type": "factor",
        "required_fields": ["turnover"],
        "lookback_days": 20,
        "horizon_days": 10,
        "family": "liquidity_quality",
        "description": (
            "A-share daily OHLCV factor: rank stocks with high average turnover and low turnover volatility. Economic "
            "thesis: stable liquidity can proxy institutional participation and capacity; validation must reject it "
            "unless after-cost long-only performance beats 000300 with controlled drawdown."
        ),
    },
    {
        "idea_id": "joinquant_small_cap_low_price",
        "title": "JoinQuant Small Cap Low Price",
        "formula_key": "joinquant_small_cap_low_price",
        "strategy_type": "factor",
        "required_fields": ["close", "market_cap"],
        "lookback_days": 1,
        "horizon_days": 5,
        "family": "joinquant_small_cap",
        "source": "joinquant_community",
        "source_url": "https://www.joinquant.com/community/post/detailMobile?postId=59884",
        "description": (
            "JoinQuant community-style A-share daily factor: within tradable non-ST stocks, keep only low nominal "
            "price names and rank by point-in-time market cap ascending. Economic thesis: A-share retail preference "
            "and small-cap style premia may concentrate in lower-priced small caps, but strict validation must include "
            "status-table ST/suspension filters, daily price limits, T+1 execution, costs, turnover, and CSI 300 excess."
        ),
    },
)


def build_ashare_structural_strategy_dicts(
    idea_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    allowed = {str(value) for value in idea_ids or []}
    rows = []
    for spec in _IDEAS:
        if allowed and spec["idea_id"] not in allowed and spec["formula_key"] not in allowed:
            continue
        rows.append(_strategy_dict(spec))
    return rows


class AShareStructuralSource:
    @property
    def source_name(self) -> str:
        return ASHARE_STRUCTURAL_SOURCE

    def search(self, query: Optional[Dict[str, Any]] = None, max_results: int = 50) -> List[Dict[str, Any]]:
        cfg = query or {}
        rows = build_ashare_structural_strategy_dicts(idea_ids=_idea_ids_from_query(cfg))
        limit = max(0, int(max_results or 0))
        return rows[:limit] if limit else []


def _strategy_dict(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": spec["title"],
        "description": spec["description"],
        "source": spec.get("source", ASHARE_STRUCTURAL_SOURCE),
        "source_url": spec.get("source_url", ASHARE_STRUCTURAL_SOURCE_URL),
        "authors": ASHARE_STRUCTURAL_AUTHORS,
        "published_date": ASHARE_STRUCTURAL_PUBLISHED_DATE,
        "metadata": {
            "external_library": "ashare_structural_daily_ideas",
            "idea_id": spec["idea_id"],
            "formula_key": spec["formula_key"],
            "strategy_type": spec["strategy_type"],
            "required_local_fields": list(spec["required_fields"]),
            "missing_daily_cn_fields": [],
            "a_share_ready": True,
            "lookback_days": int(spec["lookback_days"]),
            "horizon_days": int(spec["horizon_days"]),
            "execution_lag_days": 1,
            "factor_family": spec["family"],
            "data_table": "daily_cn_ochl",
            "implementation_note": "Uses HFQ adjusted daily OHLCV where available; strict backtest remains required.",
        },
    }


def _idea_ids_from_query(query: Dict[str, Any]) -> Optional[List[str]]:
    value = query.get("idea_ids") or query.get("ideas") or query.get("formula_key")
    if value is None:
        return None
    if isinstance(value, str):
        return [item for item in value.replace(" ", "").split(",") if item]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]
