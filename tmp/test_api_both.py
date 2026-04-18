import requests, time

symbols = ['HK.01024','HK.06618','HK.02013','HK.02057','HK.00700','HK.09988',
           'HK.03690','HK.09618','HK.02015','HK.09888','HK.09961','HK.02382',
           'HK.00981','HK.09626','HK.09901','HK.02018','HK.01810','HK.00285',
           'HK.06690','HK.00268','HK.00772']

# Test SimpleMomentum too for comparison
for strategy_id in ['volatility_regime', 'SimpleMomentum']:
    payload = {
        'strategy_id': strategy_id,
        'start_date': '2020-01-01',
        'end_date': '2024-12-31',
        'symbols': symbols,
        'initial_cash': 100000,
        'slippage_bps': 5,
    }

    t0 = time.perf_counter()
    resp = requests.post('http://localhost:5000/api/backtest/run', json=payload)
    data = resp.json()
    bid = data['backtest_id']
    post_time = time.perf_counter() - t0
    print(f'[{strategy_id}] POST: {post_time:.3f}s')

    for i in range(120):
        time.sleep(0.3)
        r = requests.get(f'http://localhost:5000/api/backtest/result/{bid}')
        result = r.json()
        status = result.get('status')
        elapsed = time.perf_counter() - t0
        if status == 'completed':
            m = result.get('metrics', {})
            print(f'  COMPLETED in {elapsed:.2f}s  NAV={m.get("final_nav"):,.2f} Return={m.get("total_return_pct"):.2f}% Sharpe={m.get("sharpe_ratio"):.2f} Trades={m.get("total_trades")}')
            break
        elif status == 'error':
            print(f'  ERROR after {elapsed:.2f}s: {result.get("error")}')
            break
    else:
        print(f'  TIMEOUT after 36s')
