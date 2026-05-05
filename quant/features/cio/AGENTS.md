# CIO Feature

## 职责

CIO 市场评估和策略权重分配。包含市场评估器、新闻分析器、权重分配器。

## 对外契约

- `CIOEngine` - CIO 引擎主类
- `MarketAssessor` - 市场评估
- `NewsAnalyzer` - 新闻分析
- `WeightAllocator` - 权重分配

## 依赖

- `shared/utils` - logger
- LLM 适配器（OpenAI/Claude/Ollama）

## 不变量

- VIX < 15: 牛市，> 25: 熊市
- 权重总和必须等于 1.0
- LLM 调用失败时优雅降级到中性默认

## 修改守则

- 改市场评估逻辑：只动 `market_assessor.py`
- 改新闻分析：只动 `news_analyzer.py`
- 改权重算法：只动 `weight_allocator.py`

## Known Pitfalls

- LLM API 调用可能超时，需要设置超时时间
- 新闻文本过长需要截断
