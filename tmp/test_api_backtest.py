import requests, time

symbols = ['HK.01024','HK.06618','HK.02013','HK.02057','HK.00700','HK.09988',
           'HK.03690','HK.09618','HK.02015','HK.09888','HK.09961','HK.02382',
           'HK.00981','HK.09626','HK.09901','HK.02018','HK.01810','HK.00285',
           'HK.06690','HK.00268','HK.00772']

payload = {
    'strategy_id': 'volatility_regime',
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
print(f'POST: {time.perf_counter()-t0:.3f}s  backtest_id={bid}')

for i in range(60):
    time.sleep(1)
    r = requests.get(f'http://localhost:5000/api/backtest/result/{bid}')
    result = r.json()
    status = result.get('status')
    elapsed = time.perf_counter() - t0
    if status == 'completed':
        m = result.get('metrics', {})
        print(f'COMPLETED in {elapsed:.2f}s')
        print(f'  NAV={m.get("final_nav"):,.2f}  Return={m.get("total_return_pct"):.2f}%  Sharpe={m.get("sharpe_ratio"):.2f}')
        print(f'  Trades={m.get("total_trades")}  MaxDD={m.get("max_drawdown_pct"):.2f}%')
        break
    elif status == 'error':
        print(f'ERROR after {elapsed:.2f}s: {result.get("error")}')
        break
    elif i % 5 == 0:
        print(f'  polling... {elapsed:.0f}s')
else:
    print(f'TIMEOUT after {time.perf_counter()-t0:.1f}s')
