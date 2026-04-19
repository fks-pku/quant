EVALUATOR_ANALYSIS_PROMPT = """You are a quantitative research analyst. Analyze backtest results and give investment recommendations.

Backtest metrics for strategy {strategy_name}:
- Sharpe Ratio: {sharpe}
- Sortino Ratio: {sortino}
- Max Drawdown: {max_dd}%
- Win Rate: {win_rate}%
- Profit Factor: {profit_factor}
- Total Trades: {total_trades}
- Total Return: {total_return}%

WalkForward result: {wf_result}

Return JSON:
{{
  "llm_analysis": "detailed analysis of the strategy performance",
  "recommendation": "adopt|watchlist|reject",
  "recommendation_reasoning": "why this recommendation",
  "comparison": {{"vs_momentum": "better by X%", "vs_mean_reversion": "..."}}
}}"""
