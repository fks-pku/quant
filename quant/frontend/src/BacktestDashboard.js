import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const detectCurrency = (symbols) => {
  const list = symbols.split(',').map(s => s.trim()).filter(Boolean);
  const markets = new Set(list.map(s => {
    if (s.startsWith('HK.')) return 'HKD';
    if (/^\d{6}$/.test(s) && '03689'.includes(s[0])) return 'CNY';
    return 'USD';
  }));
  if (markets.size === 1) return [...markets][0];
  return 'USD';
};

const fmtCurrency = (v, currency) => {
  const n = parseFloat(v) || 0;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD' }).format(n);
};

const fmtPct = (v) => {
  const n = parseFloat(v) || 0;
  return n.toFixed(2) + '%';
};

const colorPnl = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function EquityChart({ curve, currency = false }) {
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
              {fmtCurrency(v, currency)}
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

function DrawdownChart({ curve, currency = false }) {
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

function CollapsibleSection({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bt-collapsible">
      <button className="bt-collapsible-header" onClick={() => setOpen(!open)}>
        <span>{title}</span>
        <span className="bt-collapsible-arrow">{open ? '▼' : '▶'}</span>
      </button>
      {open && <div className="bt-collapsible-body">{children}</div>}
    </div>
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

  const currency = detectCurrency(symbols);

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
              { label: 'Final NAV', value: fmtCurrency(result.metrics.final_nav, currency) },
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
            <EquityChart curve={result.equity_curve} currency={currency} />
          </div>

          <div className="bt-chart">
            <div className="bt-chart-title">Drawdown</div>
            <DrawdownChart curve={result.equity_curve} currency={currency} />
          </div>

          {result.trades && result.trades.length > 0 && (() => {
            const openTrades = result.trades.filter(t => t.status === 'open');
            const closedTrades = result.trades.filter(t => t.status === 'closed');
            return (
              <>
                <CollapsibleSection title={`持仓 (${openTrades.length})`} defaultOpen={false}>
                  {openTrades.length > 0 ? (
                    <div className="bt-position-cards">
                      {openTrades.map((t, i) => {
                        const mv = t.exit_price * t.quantity;
                        const weight = result.metrics?.final_nav ? (mv / result.metrics.final_nav * 100) : 0;
                        const pnlPct = t.entry_price > 0 ? ((t.exit_price - t.entry_price) / t.entry_price * 100) : 0;
                        return (
                          <div key={i} className={`bt-position-card ${t.pnl >= 0 ? 'bt-card-profit' : 'bt-card-loss'}`}>
                            <div className="bt-card-symbol">{t.symbol}</div>
                            <div className="bt-card-pnl" style={{ color: t.pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                              {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, currency)}
                              <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                                ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                              </span>
                            </div>
                            <div className="bt-card-details">
                              <span className="bt-card-label">持仓</span>
                              <span className="bt-card-value">{t.quantity.toLocaleString()} 股</span>
                              <span className="bt-card-label">成本</span>
                              <span className="bt-card-value">{fmtCurrency(t.entry_price, currency)}</span>
                              <span className="bt-card-label">现价</span>
                              <span className="bt-card-value" style={{ color: 'var(--accent-cyan)' }}>{fmtCurrency(t.exit_price, currency)}</span>
                              <span className="bt-card-label">市值</span>
                              <span className="bt-card-value">{fmtCurrency(mv, currency)}</span>
                              <span className="bt-card-label">权重</span>
                              <span className="bt-card-value">{weight.toFixed(1)}%</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : <div style={{ color: 'var(--text-muted)', fontSize: 12, padding: 8 }}>无持仓</div>}
                </CollapsibleSection>

                <CollapsibleSection title={`已完成交易 (${closedTrades.length})`} defaultOpen={false}>
                  {closedTrades.length > 0 ? (
                    <div className="bt-trades-scroll">
                      <table className="bt-trades-table">
                        <thead>
                          <tr>
                            <th>Entry Date</th>
                            <th>Exit Date</th>
                            <th>Symbol</th>
                            <th>Qty</th>
                            <th>Entry Price</th>
                            <th>Exit Price</th>
                            <th>P&L</th>
                            <th>Return%</th>
                            <th>Duration</th>
                          </tr>
                        </thead>
                        <tbody>
                          {closedTrades.map((t, i) => {
                            const entryD = new Date(t.entry_time);
                            const exitD = new Date(t.exit_time);
                            const durDays = Math.round((exitD - entryD) / 86400000);
                            const retPct = t.entry_price > 0 ? ((t.exit_price - t.entry_price) / t.entry_price * 100) : 0;
                            return (
                              <tr key={i}>
                                <td>{entryD.toLocaleDateString()}</td>
                                <td>{exitD.toLocaleDateString()}</td>
                                <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                                <td>{t.quantity}</td>
                                <td>{fmtCurrency(t.entry_price, currency)}</td>
                                <td>{fmtCurrency(t.exit_price, currency)}</td>
                                <td style={{ color: colorPnl(t.pnl), fontWeight: 600 }}>
                                  {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, currency)}
                                </td>
                                <td style={{ color: colorPnl(retPct), fontWeight: 600 }}>
                                  {retPct >= 0 ? '+' : ''}{retPct.toFixed(1)}%
                                </td>
                                <td>{durDays}d</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : <div style={{ color: 'var(--text-muted)', fontSize: 12, padding: 8 }}>无已完成交易</div>}
                </CollapsibleSection>

                {result.trade_timeline && result.trade_timeline.length > 0 && (
                  <CollapsibleSection title={`交易流水 (${result.trade_timeline.length})`} defaultOpen={false}>
                    <div className="bt-trades-scroll" style={{ maxHeight: 400 }}>
                      <table className="bt-timeline-table">
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Action</th>
                            <th>Symbol</th>
                            <th>Qty</th>
                            <th>Price</th>
                            <th>Position</th>
                            <th>P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.trade_timeline.map((t, i) => (
                            <tr key={i}>
                              <td>{new Date(t.date).toLocaleDateString()}</td>
                              <td style={{ color: t.action === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{t.action}</td>
                              <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                              <td>{t.action === 'BUY' ? '+' : '-'}{t.quantity}</td>
                              <td>{fmtCurrency(t.price, currency)}</td>
                              <td style={{ fontWeight: 600 }}>{t.position}</td>
                              <td style={t.pnl != null ? { color: colorPnl(t.pnl), fontWeight: 600 } : { color: 'var(--text-muted)' }}>
                                {t.pnl != null ? `${t.pnl >= 0 ? '+' : ''}${fmtCurrency(t.pnl, currency)}` : '\u2014'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CollapsibleSection>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
