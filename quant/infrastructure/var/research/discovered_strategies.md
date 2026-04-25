# Discovered Strategies — 2026-04-25

Sources: arXiv q-fin (latest), Alpha Architect Blog, Quantocracy Mashup

---

## 1. Daily Return Anomaly Framework (日线收益异常统一框架)

- **来源**: [Alpha Architect / Nusret Cakici et al. (Jan 2026)](https://alphaarchitect.com/2025/01/24/unlocking-hidden-patterns-how-daily-returns-predict-future-stock-performance/)
- **发布时间**: 2026-01
- **核心思路**: 日线收益模式(正/负/连续同方向天数)可预测未来股票表现。连续上涨天数多的股票短期继续涨(动量)，但中期反转。
- **所需数据**: 日线 OHLCV
- **适用市场**: 通用 (论文测试美股，A股动量/反转效应同样存在)
- **策略类型**: momentum / mean_reversion

## 2. Growth and Inflation Sector Timing (增长通胀板块轮动)

- **来源**: [Quantocracy / David Varadi via Allocate Smartly](https://quantocracy.com/)
- **发布时间**: 2025-12
- **核心思路**: 基于增长和通胀预期状态(4象限)进行板块轮动。高增长高通胀配周期股，低增长低通胀配防御股。
- **所需数据**: 日线 OHLCV (板块ETF/指数)
- **适用市场**: A股 (行业ETF丰富：银行、券商、消费、医药、科技等)
- **策略类型**: factor / rotation

## 3. Calendar Ensemble Alpha Overlay (日历效应组合Alpha叠加)

- **来源**: [Quantocracy / Beyond Passive](https://quantocracy.com/)
- **发布时间**: 2025-11
- **核心思路**: 多个独立的日历效应(月末效应、星期效应、节假日效应)组合成alpha overlay，每个效应单独弱但组合后显著。
- **所需数据**: 日线 OHLCV + 日历数据
- **适用市场**: A股 (A股日历效应显著：春节效应、月初效应)
- **策略类型**: factor

## 4. Cross-Sectional Momentum with Regime Filter (带体制过滤的截面动量)

- **来源**: [arXiv:2604.18821 / Chang Liu (Apr 2026)](https://arxiv.org/abs/2604.18821)
- **发布时间**: 2026-04
- **核心思路**: 策略在因子环境极端好之后上线表现差。据此设计：动量信号只在因子环境非极端时启用，极端时降低仓位或切换防御。
- **所需数据**: 日线 OHLCV + 市场波动率代理
- **适用市场**: 通用
- **策略类型**: momentum / regime

## 5. Trend-Following with Volatility Scaling (波动率缩放的趋势跟踪)

- **来源**: [Alpha Architect DIY Trend-Following (Jan 2025)](https://alphaarchitect.com/2025/01/24/diy-trend-following-allocations-january-2025/) + [Quantpedia Tactical Allocation](https://quantocracy.com/)
- **发布时间**: 2025-01
- **核心思路**: 多资产趋势跟踪(SMA信号)，按波动率倒数分配仓位(低波动多配)，月度调仓。2025年4月关税冲击中表现优异。
- **所需数据**: 日线 OHLCV (多ETF/指数)
- **适用市场**: A股 (可用沪深300、中证500、创业板、国债ETF等)
- **策略类型**: momentum / trend_following

## 6. Post-Screening Portfolio Selection (筛选后组合选择)

- **来源**: [arXiv:2604.17593 / Uematsu & Tanaka (Apr 2026)](https://arxiv.org/abs/2604.17593)
- **发布时间**: 2026-04
- **核心思路**: 先用因子筛选候选股票(如动量top30%)，再用稀疏优化从中选出最优组合，减少估计误差。比纯因子排序或纯均值方差更稳健。
- **所需数据**: 日线 OHLCV
- **适用市场**: 通用
- **策略类型**: factor / portfolio

## 7. Friday Gold / VIX Conditional Trade (VIX条件交易)

- **来源**: [Quantocracy / Beyond Passive](https://quantocracy.com/)
- **发布时间**: 2025-10
- **核心思路**: VIX期限结构压缩时，机构周四下午买入周末保护，驱动黄金周五上涨。条件性日历效应。
- **所需数据**: 日线 OHLCV + VIX或波动率代理
- **适用市场**: 通用 (需适配A股的波动率代理)
- **策略类型**: factor / calendar

## 8. Selling Volatility PutWrite (卖出波动率策略)

- **来源**: [Quantocracy / Quantt](https://quantocracy.com/)
- **发布时间**: 2025-09
- **核心思路**: 持续卖出平值看跌期权(用国债作抵押)，30年回测Sharpe ~0.9。本质是收取风险溢价。无法直接用日线OHLCV实现(需期权数据)。
- **所需数据**: 期权数据
- **适用市场**: 需要期权市场
- **策略类型**: volatility_selling
