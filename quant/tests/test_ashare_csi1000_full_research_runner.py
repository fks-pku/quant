import importlib


def test_csi1000_full_research_runner_uses_csi1000_contract():
    runner = importlib.import_module("quant.scripts.run_ashare_csi1000_strict_index_enhanced_full_research")

    assert runner.STRATEGY_ID == "ashare_csi1000_strict_index_enhanced"
    assert runner.INDEX_CODE == "000852.SH"
    assert runner.BENCHMARK_SYMBOL == "000852"
    assert runner.STRATEGY_PARAMS["benchmark_symbol"] == "000852"
    assert runner.STRATEGY_PARAMS["max_positions"] == 120
    assert runner.STRATEGY_PARAMS["max_single_weight"] == 0.055
