import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const fmtCurrency = (v, isHK) => {
  const n = parseFloat(v) || 0;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: isHK ? 'HKD' : 'USD' }).format(n);
};

const fmtPct = (v) => {
  const n = parseFloat(v) || 0;
  return n.toFixed(2) + '%';
};

const colorPnl = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function EquityChart({ curve, isHK = false }) {
  if (!curve || curve.length < 2) return null;

  const W = 700;
  const H = 200;
  const padL = 60;
  const padR = 20;
  const padT = 10;
  const padB = 30;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const values = curve.map(([, v]) => v);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const rangeV = maxV - minV || 1;

  const scaleX = (i) => padL + (i / (curve.length - 1)) * plotW;
  const scaleY = (v) => padT + plotH - ((v - minV) / rangeV) * plotH;

  const points = curve.map(([d, v], i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  const areaPoints = `${scaleX(0)},${padT + plotH} ${points} ${scaleX(curve.length - 1)},${padT + plotH}`;

  const gridLines = 5;
  const gridYVals = Array.from({ length: gridLines }, (_, i) => minV + (rangeV * i) / (gridLines - 1));

  const dateLabels = [];
  const mid = Math.floor(curve.length / 2);
  [0, mid, curve.length - 1].forEach((idx) => {
    dateLabels.push({ x: scaleX(idx), label: curve[idx][0].slice(0, 10) });
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: '200px' }}>
      <defs>
        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#00d4ff" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {gridYVals.map((v, i) => {
        const y = scaleY(v);
        return (
          <g key={i}>
            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#333355" strokeWidth="0.5" />
            <text x={padL - 6} y={y + 3} textAnchor="end" fill="#666680" fontSize="9">
              {fmtCurrency(v, isHK)}
            </text>
          </g>
        );
      })}
      <polygon points={areaPoints} fill="url(#eqGrad)" />
      <polyline points={points} fill="none" stroke="#00d4ff" strokeWidth="1.5" />
      {dateLabels.map((dl, i) => (
        <text key={i} x={dl.x} y={H - 6} textAnchor="middle" fill="#666680" fontSize="9">
          {dl.label}
        </text>
      ))}
    </svg>
  );
}

function DrawdownChart({ curve, isHK = false }) {
  if (!curve || curve.length < 2) return null;

  const W = 700;
  const H = 100;
  const padL = 60;
  const padR = 20;
  const padT = 10;
  const padB = 20;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  let peak = curve[0][1];
  const drawdowns = curve.map(([, v]) => {
    if (v > peak) peak = v;
    return (v - peak) / peak;
  });
  const minDD = Math.min(...drawdowns, 0);

  const scaleX = (i) => padL + (i / (curve.length - 1)) * plotW;
  const scaleY = (dd) => padT + (Math.abs(dd) / Math.abs(minDD || -1)) * plotH;
  const zeroY = padT;

  const points = drawdowns.map((dd, i) => `${scaleX(i)},${scaleY(dd)}`).join(' ');
  const areaPoints = `${scaleX(0)},${zeroY} ${points} ${scaleX(curve.length - 1)},${zeroY}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: '100px' }}>
      <line x1={padL} y1={zeroY} x2={W - padR} y2={zeroY} stroke="#333355" strokeWidth="0.5" />
      <polygon points={areaPoints} fill="rgba(255, 51, 102, 0.3)" />
      <polyline points={points} fill="none" stroke="#ff3366" strokeWidth="1" />
      <text x={padL - 6} y={zeroY + 3} textAnchor="end" fill="#666680" fontSize="9">0%</text>
      <text x={padL - 6} y={padT + plotH} textAnchor="end" fill="#666680" fontSize="9">
        {(minDD * 100).toFixed(1)}%
      </text>
    </svg>
  );
}

export default function BacktestDashboard() {
  const [strategy, setStrategy] = useState('SimpleMomentum');
  const [startDate, setStartDate] = useState('2020-01-01');
  const [endDate, setEndDate] = useState('2024-12-31');
  const [symbols, setSymbols] = useState('HK.00700');
  const [initialCash, setInitialCash] = useState(100000);
  const [slippageBps, setSlippageBps] = useState(5);
  const [status, setStatus] = useState('idle');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [history, setHistory] = useState([]);
  const pollRef = useRef(null);
  const [strategies, setStrategies] = useState([]);
  const [strategyParams, setStrategyParams] = useState({});
  const [paramValues, setParamValues] = useState({});
  const symbolsRef = useRef('HK.00700');
  const userEditedSymbols = useRef(false);

  const fetchHistory = async () => {
    try {
      const res = await axios.get(`${API_BASE}/backtest/list`);
      setHistory(res.data.backtests || []);
    } catch {}
  };

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      const strats = res.data.strategies || [];
      setStrategies(strats);
      if (strats.length > 0 && !strats.find(s => s.id === strategy)) {
        setStrategy(strats[0].id);
      }
    } catch (e) { console.error('Failed to fetch strategies:', e); }
  }, [strategy]);

  const fetchParams = useCallback(async (strategyId) => {
    try {
      const res = await axios.get(`${API_BASE}/strategies/${strategyId}/parameters`);
      const params = res.data.parameters || {};
      setStrategyParams(params);
      const defaults = {};
      Object.entries(params).forEach(([key, def]) => {
        defaults[key] = def.default;
      });
      setParamValues(defaults);
    } catch (e) {
      setStrategyParams({});
      setParamValues({});
    }
  }, []);

  const getDefaultSymbols = useCallback((strategyId) => {
    const found = strategies.find(s => s.id === strategyId);
    return found?.default_symbols || 'HK.00700';
  }, [strategies]);


  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  useEffect(() => {
    if (strategy) {
      fetchParams(strategy);
      if (!userEditedSymbols.current) {
        const defaultSym = getDefaultSymbols(strategy);
        setSymbols(defaultSym);
        symbolsRef.current = defaultSym;
      }
    }
  }, [strategy, fetchParams, getDefaultSymbols]);

  useEffect(() => {
    fetchHistory();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const pollResult = (backtestId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    setStatus('running');
    setError('');

    pollRef.current = setInterval(async () => {
      try {
        const res = await axios.get(`${API_BASE}/backtest/result/${backtestId}`);
        if (res.data.status === 'completed') {
          setResult(res.data);
          setStatus('completed');
          clearInterval(pollRef.current);
          pollRef.current = null;
          fetchHistory();
        } else if (res.data.status === 'error') {
          setError(res.data.error || res.data.description || 'Backtest failed');
          setStatus('error');
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch (err) {
        setError(err.message);
        setStatus('error');
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 2000);
  };

  const runBacktest = async () => {
    setStatus('running');
    setError('');
    setResult(null);
    try {
      const res = await axios.post(`${API_BASE}/backtest/run`, {
        strategy_id: strategy,
        start_date: startDate,
        end_date: endDate,
        symbols: symbols.split(',').map(s => s.trim()).filter(Boolean),
        initial_cash: Number(initialCash),
        slippage_bps: Number(slippageBps),
        strategy_params: paramValues,
      });
      pollResult(res.data.backtest_id);
    } catch (err) {
      setError(err.response?.data?.error || err.message);
      setStatus('error');
    }
  };

  const loadResult = async (btId) => {
    setStatus('running');
    setError('');
    try {
      const res = await axios.get(`${API_BASE}/backtest/result/${btId}`);
      if (res.data.status === 'completed') {
        setResult(res.data);
        setStatus('completed');
      } else if (res.data.status === 'running') {
        pollResult(btId);
      } else {
        setError(res.data.error || res.data.description || 'Backtest failed');
        setStatus('error');
      }
    } catch (err) {
      setError(err.message);
      setStatus('error');
    }
  };

  const statusText = { idle: 'Idle', running: 'Running...', completed: 'Completed', error: 'Error' };
  const statusColor = { idle: 'var(--text-muted)', running: 'var(--accent-amber)', completed: 'var(--accent-green)', error: 'var(--accent-red)' };

  const isHK = symbols.split(',').map(s => s.trim()).some(s => s.startsWith('HK.'));

  return (
    <div className="bt-dashboard">
      <div className="bt-controls">
        <div className="bt-control-group">
          <label>Strategy</label>
          <select className="bt-select" value={strategy} onChange={e => setStrategy(e.target.value)}>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div className="bt-control-group">
          <label>Start Date</label>
          <input type="date" className="bt-input" value={startDate} onChange={e => setStartDate(e.target.value)} />
        </div>
        <div className="bt-control-group">
          <label>End Date</label>
          <input type="date" className="bt-input" value={endDate} onChange={e => setEndDate(e.target.value)} />
        </div>
        <div className="bt-control-group" style={{ minWidth: 200 }}>
          <label>Symbols</label>
          <input type="text" className="bt-input" value={symbols} onChange={e => { setSymbols(e.target.value); userEditedSymbols.current = true; }} />
        </div>
        <div className="bt-control-group">
          <label>Initial Cash</label>
          <input type="number" className="bt-input" value={initialCash} onChange={e => setInitialCash(e.target.value)} />
        </div>
        <div className="bt-control-group">
          <label>Slippage (bps)</label>
          <input type="number" className="bt-input" value={slippageBps} onChange={e => setSlippageBps(e.target.value)} />
        </div>
        <button className="bt-run-btn" onClick={runBacktest} disabled={status === 'running'}>
          {status === 'running' ? 'RUNNING...' : 'RUN BACKTEST'}
        </button>
        <span className="bt-status" style={{ color: statusColor[status] }}>{statusText[status]}</span>
      </div>

      {error && (
        <div className="bt-controls" style={{ color: 'var(--accent-red)', fontSize: '13px', marginBottom: 20 }}>
          {error}
        </div>
      )}

      {Object.keys(strategyParams).length > 0 && (
        <div className="bt-controls bt-params-section">
          <div style={{ width: '100%', fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Strategy-Specific Parameters
          </div>
          {Object.entries(strategyParams).map(([key, def]) => (
            <div key={key} className="bt-control-group">
              <label>{key.replace(/_/g, ' ')} <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>({def.type})</span></label>
              {def.type === 'bool' ? (
                <label className="toggle-switch" style={{ marginTop: 4 }}>
                  <input type="checkbox" checked={!!paramValues[key]} onChange={e => setParamValues(prev => ({ ...prev, [key]: e.target.checked }))} />
                  <span className="toggle-slider"></span>
                </label>
              ) : def.options ? (
                <select className="bt-input" value={paramValues[key] ?? def.default} onChange={e => setParamValues(prev => ({ ...prev, [key]: e.target.value }))}>
                  {def.options.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input type={def.type === 'int' || def.type === 'float' ? 'number' : 'text'} className="bt-input"
                  value={paramValues[key] ?? def.default}
                  step={def.type === 'float' ? '0.1' : undefined}
                  onChange={e => setParamValues(prev => ({ ...prev, [key]: def.type === 'int' ? parseInt(e.target.value) : def.type === 'float' ? parseFloat(e.target.value) : e.target.value }))}
                />
              )}
              <span style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{def.description}</span>
            </div>
          ))}
        </div>
      )}

      {history.length > 0 && (
        <div className="bt-history">
          <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Past Backtests
          </div>
          {history.map((bt) => (
            <div key={bt.backtest_id} className="bt-history-item" onClick={() => loadResult(bt.backtest_id)}>
              <span style={{ color: 'var(--text-primary)', fontSize: 13 }}>{bt.strategy_id} — {bt.backtest_id.slice(0, 8)}</span>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {bt.total_return_pct != null ? `${bt.total_return_pct.toFixed(2)}%` : bt.status}
                {bt.sharpe_ratio != null ? ` | SR: ${bt.sharpe_ratio.toFixed(2)}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {result && result.metrics && (
        <div className="bt-results">
          <div className="bt-metrics">
            {[
              { label: 'Final NAV', value: fmtCurrency(result.metrics.final_nav, isHK) },
              { label: 'Total Return', value: fmtPct(result.metrics.total_return_pct), color: colorPnl(result.metrics.total_return_pct) },
              { label: 'Sharpe Ratio', value: (result.metrics.sharpe_ratio || 0).toFixed(2) },
              { label: 'Sortino Ratio', value: (result.metrics.sortino_ratio || 0).toFixed(2) },
              { label: 'Max Drawdown', value: fmtPct(result.metrics.max_drawdown_pct), color: 'var(--accent-red)' },
              { label: 'Win Rate', value: fmtPct(result.metrics.win_rate) },
              { label: 'Profit Factor', value: (result.metrics.profit_factor || 0).toFixed(2) },
              { label: 'Total Trades', value: result.metrics.total_trades },
            ].map((m, i) => (
              <div key={i} className="bt-metric">
                <div className="bt-metric-value" style={m.color ? { color: m.color } : {}}>{m.value}</div>
                <div className="bt-metric-label">{m.label}</div>
              </div>
            ))}
          </div>

          <div className="bt-chart">
            <div className="bt-chart-title">Equity Curve</div>
            <EquityChart curve={result.equity_curve} isHK={isHK} />
          </div>

          <div className="bt-chart">
            <div className="bt-chart-title">Drawdown</div>
            <DrawdownChart curve={result.equity_curve} isHK={isHK} />
          </div>

          {result.trades && result.trades.length > 0 && (
            <div className="bt-trades">
              <div className="bt-trades-title">Trades ({result.trades.length})</div>
              <div className="bt-trades-scroll">
                <table className="bt-trades-table">
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Entry Date</th>
                      <th>Exit Date</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Entry Price</th>
                      <th>Exit Price</th>
                      <th>P&L</th>
                      <th>Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => {
                      const isOpen = t.status === 'open';
                      const entryD = new Date(t.entry_time);
                      const exitD = t.exit_time ? new Date(t.exit_time) : null;
                      const durMs = exitD ? exitD - entryD : 0;
                      const durDays = Math.round(durMs / 86400000);
                      return (
                        <tr key={i} className={isOpen ? 'trade-open' : ''}>
                          <td>
                            <span className={`bt-status-badge ${isOpen ? 'bt-status-open' : 'bt-status-closed'}`}>
                              {isOpen ? '持仓中' : '已完成'}
                            </span>
                          </td>
                          <td>{entryD.toLocaleDateString()}</td>
                          <td>{exitD ? exitD.toLocaleDateString() : '—'}</td>
                          <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                          <td style={{ color: 'var(--accent-green)', fontWeight: 600 }}>{t.side}</td>
                          <td>{t.quantity}</td>
                          <td>{fmtCurrency(t.entry_price, isHK)}</td>
                          <td style={isOpen ? { color: 'var(--accent-cyan)' } : {}}>
                            {fmtCurrency(t.exit_price, isHK)}
                          </td>
                          <td className={isOpen ? 'bt-pnl-unrealized' : ''} style={{ color: isOpen ? 'var(--accent-amber)' : colorPnl(t.pnl), fontWeight: 600 }}>
                            {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, isHK)}
                          </td>
                          <td>{isOpen ? '—' : `${durDays}d`}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
