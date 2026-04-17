import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import BacktestDashboard from './BacktestDashboard';
import StrategyPoolPage from './StrategyPoolPage';
import StrategyWeightBar from './StrategyWeightBar';
import './App.css';

const API_BASE = 'http://localhost:5000/api';

const BROKERS = [
  { id: 'paper', name: 'Paper Trading' },
  { id: 'futu', name: 'Futu (HK/US)' },
  { id: 'ibkr', name: 'Interactive Brokers' },
  { id: 'alpaca', name: 'Alpaca' },
];

function StrategyDetail({ strategyId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!strategyId) return;
    setLoading(true);
    axios.get(`${API_BASE}/strategies/performance/${strategyId}`)
      .then(res => setData(res.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [strategyId]);

  if (loading) return <div className="empty-text">Loading strategy data...</div>;
  if (!data) return <div className="empty-text">Select a strategy to view details</div>;

  const { performance: perf, recent_trades: trades, pnl_curve: curve, description } = data;

  const maxPnl = Math.max(...curve.map(Math.abs), 1);
  const chartH = 80;
  const chartW = curve.length > 1 ? 100 / (curve.length - 1) : 100;

  const buildPath = () => {
    if (curve.length < 2) return '';
    const mid = chartH / 2;
    return curve.map((v, i) => {
      const x = i * chartW;
      const y = mid - (v / maxPnl) * (chartH / 2 - 4);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  };

  const fmtCurrency = (v) => {
    const n = parseFloat(v) || 0;
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
  };
  const pnlCol = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  return (
    <div className="strategy-detail">
      <div className="sd-description">{description}</div>

      <div className="sd-section-title">KEY METRICS</div>
      <div className="sd-metrics">
        <div className="sd-metric">
          <div className="sd-metric-val" style={{ color: pnlCol(perf.total_pnl) }}>{fmtCurrency(perf.total_pnl)}</div>
          <div className="sd-metric-label">Total P&L</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val">{perf.sharpe_ratio}</div>
          <div className="sd-metric-label">Sharpe Ratio</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val" style={{ color: 'var(--accent-red)' }}>{perf.max_drawdown}%</div>
          <div className="sd-metric-label">Max Drawdown</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val" style={{ color: 'var(--accent-green)' }}>{perf.cagr}%</div>
          <div className="sd-metric-label">CAGR</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val">{perf.total_trades}</div>
          <div className="sd-metric-label">Total Trades</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val">{perf.win_rate}%</div>
          <div className="sd-metric-label">Win Rate</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val">{perf.profit_factor}</div>
          <div className="sd-metric-label">Profit Factor</div>
        </div>
        <div className="sd-metric">
          <div className="sd-metric-val" style={{ color: pnlCol(perf.avg_win) }}>{fmtCurrency(perf.avg_win)}</div>
          <div className="sd-metric-label">Avg Win</div>
        </div>
      </div>

      <div className="sd-section-title">EQUITY CURVE</div>
      <div className="sd-chart-container">
        <svg viewBox={`0 0 100 ${chartH}`} className="sd-chart" preserveAspectRatio="none">
          <line x1="0" y1={chartH / 2} x2="100" y2={chartH / 2} stroke="var(--border-color)" strokeWidth="0.5" />
          <path d={buildPath()} fill="none" stroke="var(--accent-cyan)" strokeWidth="0.8" />
        </svg>
        <div className="sd-chart-labels">
          <span>{fmtCurrency(maxPnl)}</span>
          <span>$0</span>
          <span>{fmtCurrency(-maxPnl)}</span>
        </div>
      </div>

      <div className="sd-section-title">RECENT TRADES</div>
      <div className="sd-trades">
        {trades.length === 0 ? (
          <div className="empty-text">No trades</div>
        ) : (
          <table className="sd-trades-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Side</th>
                <th>Symbol</th>
                <th>Qty</th>
                <th>Price</th>
                <th>P&L</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i}>
                  <td className="sd-trade-time">{new Date(t.time).toLocaleDateString()}</td>
                  <td style={{ color: t.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{t.side}</td>
                  <td className="sd-trade-sym">{t.symbol}</td>
                  <td>{t.quantity}</td>
                  <td>${t.price}</td>
                  <td style={{ color: pnlCol(t.pnl) }}>{t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function App() {
  const [selectedBroker, setSelectedBroker] = useState('paper');
  const [systemStatus, setSystemStatus] = useState('stopped');
  const [apiConnected, setApiConnected] = useState(false);
  const [portfolio, setPortfolio] = useState({
    nav: 100000, total_unrealized_pnl: 0, total_realized_pnl: 0, total_pnl: 0, holdings: []
  });
  const [availableStrategies, setAvailableStrategies] = useState([]);
  const [activeStrategies, setActiveStrategies] = useState([]);
  const [marketData, setMarketData] = useState([]);
  const [orders, setOrders] = useState([]);
  const [orderForm, setOrderForm] = useState({ symbol: '', quantity: '', side: 'BUY' });
  const [isLoading, setIsLoading] = useState(false);
  const [selectedStrategyId, setSelectedStrategyId] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [activeTab, setActiveTab] = useState('backtest');
  const [cioData, setCioData] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/status`);
      setApiConnected(true);
      setSystemStatus(res.data.status);
      setPortfolio({
        nav: res.data.portfolio?.nav || 100000,
        total_unrealized_pnl: res.data.portfolio?.total_unrealized_pnl || 0,
        total_realized_pnl: res.data.portfolio?.total_realized_pnl || 0,
        total_pnl: (res.data.portfolio?.total_unrealized_pnl || 0) + (res.data.portfolio?.total_realized_pnl || 0),
        holdings: res.data.positions || []
      });
      setActiveStrategies(res.data.strategies || []);
      if (res.data.selected_strategy) {
        const stratEntry = Object.entries({
          'VolatilityRegime': 'volatility_regime', 'SimpleMomentum': 'simple_momentum',
          'MomentumEOD': 'momentum_eod', 'MeanReversion1m': 'mean_reversion_1m', 'DualThrust': 'dual_thrust'
        }).find(([, id]) => id === res.data.selected_strategy);
        if (stratEntry) setSelectedStrategyId(stratEntry[1]);
      }
    } catch {
      setApiConnected(false);
      setSystemStatus('stopped');
    }
  }, []);

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      const strats = res.data.strategies || [];
      setAvailableStrategies(strats);
      if (!selectedStrategyId && strats.length > 0) {
        const enabled = strats.find(s => s.enabled);
        setSelectedStrategyId(enabled ? enabled.id : strats[0].id);
      }
    } catch (err) {
      console.error('Failed to fetch strategies:', err);
    }
  }, [selectedStrategyId]);

  const fetchMarketData = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/market`);
      setMarketData(res.data);
    } catch {
      console.error('Failed to fetch market data');
    }
  }, []);

  const fetchCIO = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/cio/assessment`);
      setCioData(res.data);
    } catch (e) { console.error('CIO fetch error', e); }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchStrategies();
    fetchMarketData();
    fetchCIO();
    const statusInterval = setInterval(fetchStatus, 3000);
    const marketInterval = setInterval(fetchMarketData, 5000);
    const cioInterval = setInterval(fetchCIO, 60000);
    return () => {
      clearInterval(statusInterval);
      clearInterval(marketInterval);
      clearInterval(cioInterval);
    };
  }, [fetchStatus, fetchStrategies, fetchMarketData, fetchCIO]);

  const startSystem = async () => {
    setIsLoading(true); setSubmitError('');
    try {
      await axios.post(`${API_BASE}/start`, { broker: selectedBroker });
      await new Promise(r => setTimeout(r, 1000));
      fetchStatus();
    } catch (err) { setSubmitError(err.response?.data?.error || err.message); }
    setIsLoading(false);
  };

  const stopSystem = async () => {
    setIsLoading(true); setSubmitError('');
    try {
      await axios.post(`${API_BASE}/stop`);
      await new Promise(r => setTimeout(r, 500));
      fetchStatus();
    } catch (err) { setSubmitError(err.response?.data?.error || err.message); }
    setIsLoading(false);
  };

  const submitOrder = async () => {
    if (!orderForm.symbol || !orderForm.quantity) return;
    setIsLoading(true); setSubmitError('');
    try {
      const res = await axios.post(`${API_BASE}/orders`, {
        symbol: orderForm.symbol.toUpperCase(), quantity: parseInt(orderForm.quantity),
        side: orderForm.side, broker: selectedBroker
      });
      setOrders(prev => [res.data, ...prev].slice(0, 20));
      setOrderForm({ symbol: '', quantity: '', side: 'BUY' });
      fetchStatus();
    } catch (err) { setSubmitError(err.response?.data?.error || err.message); }
    setIsLoading(false);
  };

  const selectStrategy = async (strategyId) => {
    setSelectedStrategyId(strategyId);
    try { await axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategyId }); }
    catch (err) { console.error('Failed to select strategy:', err); }
  };

  const formatCurrency = (val) => {
    const num = parseFloat(val) || 0;
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
  };
  const pnlColor = (val) => val >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  const getStatusBadge = () => {
    if (!apiConnected) return { text: 'DISCONNECTED', color: 'var(--accent-red)' };
    switch (systemStatus) {
      case 'running': return { text: 'RUNNING', color: 'var(--accent-green)' };
      case 'starting': return { text: 'STARTING', color: 'var(--accent-amber)' };
      case 'stopping': return { text: 'STOPPING', color: 'var(--accent-amber)' };
      default: return { text: 'STOPPED', color: 'var(--text-muted)' };
    }
  };
  const statusBadge = getStatusBadge();

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="logo-text">QUANT<span className="logo-accent">SYSTEM</span></span>
          <span className="status-badge" style={{ borderColor: statusBadge.color, color: statusBadge.color }}>
            {statusBadge.text}
          </span>
        </div>
        <div className="header-right">
          <select className="broker-select" value={selectedBroker} onChange={(e) => setSelectedBroker(e.target.value)}>
            {BROKERS.map(b => (<option key={b.id} value={b.id}>{b.name}</option>))}
          </select>
          {systemStatus === 'running' ? (
            <button className="btn btn-stop" onClick={stopSystem} disabled={isLoading}>■ STOP</button>
          ) : (
            <button className="btn btn-start" onClick={startSystem} disabled={isLoading}>▶ START</button>
          )}
        </div>
      </header>

      {submitError && (
        <div style={{ background: 'var(--accent-red)', color: '#fff', padding: '8px 16px', fontSize: '13px' }}>{submitError}</div>
      )}

      <div className="tab-bar">
        <button className={`tab ${activeTab === 'backtest' ? 'active' : ''}`} onClick={() => setActiveTab('backtest')}>BACKTEST</button>
        <button className={`tab ${activeTab === 'live' ? 'active' : ''}`} onClick={() => setActiveTab('live')}>LIVE TRADING</button>
        <button className={`tab ${activeTab === 'strategy_pool' ? 'active' : ''}`} onClick={() => setActiveTab('strategy_pool')}>STRATEGY POOL</button>
      </div>

      <main className="main">
        {activeTab === 'backtest' ? <BacktestDashboard /> : activeTab === 'strategy_pool' ? <StrategyPoolPage /> : (
        <div className="panel-grid">
          <div className="panel">
            <div className="panel-header">📊 ASSET OVERVIEW</div>
            <div className="panel-content">
              <div className="metric-grid">
                <div className="metric-box">
                  <div className="metric-label">NAV</div>
                  <div className="metric-value">{formatCurrency(portfolio.nav)}</div>
                </div>
                <div className="metric-box">
                  <div className="metric-label">Unrealized P&L</div>
                  <div className="metric-value" style={{ color: pnlColor(portfolio.total_unrealized_pnl) }}>
                    {formatCurrency(portfolio.total_unrealized_pnl)}
                  </div>
                </div>
                <div className="metric-box">
                  <div className="metric-label">Realized P&L</div>
                  <div className="metric-value" style={{ color: pnlColor(portfolio.total_realized_pnl) }}>
                    {formatCurrency(portfolio.total_realized_pnl)}
                  </div>
                </div>
                <div className="metric-box">
                  <div className="metric-label">Total P&L</div>
                  <div className="metric-value" style={{ color: pnlColor(portfolio.total_pnl) }}>
                    {formatCurrency(portfolio.total_pnl)}
                  </div>
                </div>
              </div>
              <div className="section-label">HOLDINGS</div>
              <div className="holdings-list">
                {portfolio.holdings.length === 0 ? (
                  <div className="empty-text">No positions</div>
                ) : (
                  portfolio.holdings.map((h, i) => (
                    <div key={i} className="holding-item">
                      <span>{h.symbol}</span>
                      <span>{h.quantity} shares</span>
                      <span style={{ color: pnlColor(h.pnl || 0), fontSize: '12px' }}>{formatCurrency(h.pnl || 0)}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">⚡ STRATEGY ZONE</div>
            <div className="panel-content">
              <select className="strategy-select" value={selectedStrategyId} onChange={(e) => selectStrategy(e.target.value)}>
                {availableStrategies.map(s => (
                  <option key={s.id} value={s.id}>{s.name} {s.enabled ? '(Active)' : ''}</option>
                ))}
              </select>
              <div className="active-strategy-card">
                <div className="active-strategy-header">
                  <span>Active Strategy</span>
                  <span className={systemStatus === 'running' ? 'status-running' : ''} style={systemStatus !== 'running' ? { color: 'var(--text-muted)' } : {}}>
                    {systemStatus === 'running' ? '● Running' : '○ Idle'}
                  </span>
                </div>
                <div className="active-strategy-name">
                  {availableStrategies.find(s => s.id === selectedStrategyId)?.name || 'None'}
                </div>
                <div className="active-strategy-meta">Regime: BULL | Signals: {activeStrategies.length}</div>
                {cioData && Object.keys(cioData.weights || {}).length > 0 && (
                  <>
                    <StrategyWeightBar weights={cioData.weights} />
                    <div style={{ marginTop: '8px' }}>
                      <button className="btn-link" onClick={() => setActiveTab('strategy_pool')}>
                        → Go to Strategy Pool
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">📈 MARKET DATA</div>
            <div className="panel-content">
              {marketData.length === 0 ? (
                <div className="empty-text">Loading market data...</div>
              ) : (
                marketData.map((m, i) => (
                  <div key={i} className="market-item">
                    <span className="market-symbol">{m.symbol}</span>
                    <span className="market-price">${m.price}</span>
                    <span className="market-change" style={{ color: m.change >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                      {m.change >= 0 ? '+' : ''}{m.change}%
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">📝 ORDER ZONE</div>
            <div className="panel-content">
              <div className="order-form">
                <input className="order-input" placeholder="Symbol" value={orderForm.symbol}
                  onChange={(e) => setOrderForm(prev => ({ ...prev, symbol: e.target.value }))} />
                <input className="order-input order-qty" placeholder="Qty" value={orderForm.quantity}
                  onChange={(e) => setOrderForm(prev => ({ ...prev, quantity: e.target.value }))} />
                <select className="order-select" value={orderForm.side}
                  onChange={(e) => setOrderForm(prev => ({ ...prev, side: e.target.value }))}>
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </div>
              <button className="btn btn-submit" onClick={submitOrder}
                disabled={isLoading || !orderForm.symbol || !orderForm.quantity}>Submit Order</button>
              <div className="section-label">RECENT ORDERS</div>
              <div className="orders-list">
                {orders.length === 0 ? (
                  <div className="empty-text">No orders</div>
                ) : (
                  orders.map((o, i) => (
                    <div key={i} className="order-item">
                      <span>{o.side} {o.symbol} x{o.quantity} @ ${o.price}</span>
                      <span className="order-status" style={{ color: 'var(--accent-green)' }}>{o.status}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="panel panel-wide">
            <div className="panel-header">📋 STRATEGY DETAIL — {availableStrategies.find(s => s.id === selectedStrategyId)?.name || 'None'}</div>
            <div className="panel-content">
              <StrategyDetail strategyId={selectedStrategyId} />
            </div>
          </div>
        </div>
        )}
      </main>
    </div>
  );
}

export default App;
