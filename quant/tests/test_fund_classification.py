from quant.infrastructure.data.fund_classification import classify_cn_fund
from quant.infrastructure.research.cn_etf_universe import classify_gold_equity_barbell_category


def test_fund_classification_maps_core_rotation_categories():
    assert classify_cn_fund({"name": "沪深300ETF", "index_code": "000300.SH"}).fund_category == "equity_cn_broad_csi300"
    assert classify_cn_fund({"name": "创业板50ETF", "index_name": "创业板50指数"}).category_group == "chinext50"
    assert classify_cn_fund({"name": "黄金ETF", "index_name": "黄金9999"}).fund_category == "commodity_gold"
    assert classify_cn_fund({"name": "红利ETF", "index_code": "000922.CSI"}).category_group == "dividend"


def test_fund_classification_excludes_enhanced_and_feeder_from_core_groups():
    enhanced = classify_cn_fund({"name": "沪深300ETF增强", "index_name": "沪深300指数"})
    feeder = classify_cn_fund({"name": "沪深300ETF联接", "index_name": "沪深300指数"})

    assert enhanced.classification_excluded is True
    assert enhanced.category_group == "enhanced"
    assert feeder.classification_excluded is True
    assert classify_gold_equity_barbell_category("沪深300ETF增强", "沪深300指数") is None
    assert classify_gold_equity_barbell_category("沪深300ETF", "沪深300指数") == "csi300"


def test_fund_classification_uses_tushare_benchmark_category_for_unknown_index():
    classification = classify_cn_fund(
        {
            "name": "核心宽基ETF",
            "index_code": "000999.SH",
            "index_name": "核心宽基指数",
            "bmk_type": "宽基",
            "idx_type": "规模类指数",
            "bmk_level": "一类库",
        }
    )

    assert classification.classification_source == "mkt_idx_bmk"
    assert classification.fund_strategy == "broad"
    assert classification.fund_category == "equity_cn_broad_index"
    assert classification.category_group == "broad_index"
    assert classification.classification_confidence == 0.95


def test_fund_classification_uses_tushare_benchmark_asset_class_before_equity_fallback():
    classification = classify_cn_fund(
        {
            "name": "核心固收ETF",
            "index_code": "931999.CSI",
            "index_name": "核心固收指数",
            "bmk_type": "债券",
            "idx_type": "债券指数",
        }
    )

    assert classification.classification_source == "mkt_idx_bmk"
    assert classification.asset_class == "bond"
    assert classification.fund_category == "bond_credit_or_aggregate"
    assert classification.category_group == "bond"
