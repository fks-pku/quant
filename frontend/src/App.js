import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import './App.css';

const API_BASE = 'http://localhost:5000/api';

const BROKERS = [
  { id: 'paper', name: 'Paper Trading' },
  { id: 'futu', name: 'Futu (HK/US)' },
  { id: 'ibkr', name: 'Interactive Brokers' },
  { id: 'alpaca', name: 'Alpaca' },
];

function App() {
  const [selectedBroker, setSelectedBroker] = useState('paper');
  const [systemStatus, setSystemStatus] = useState('stopped');
  const [apiConnected, setApiConnected] = useState(false);
  const [portfolio, setPortfolio] = useState({
    nav: 100000,
    total_unrealized_pnl: 0,
    total_realized_pnl: 0,
    total_pnl: 0,
    holdings: []
  });
  const [availableStrategies, setAvailableStrategies] = useState([]);
  const [activeStrategies, setActiveStrategies] = useState([]);
  const [marketData, setMarketData] = useState([]);
  const [orders, setOrders] = useState([]);
  const [orderForm, setOrderForm] = useState({ symbol: '', quantity: '', side: 'BUY' });
  const [isLoading, setIsLoading] = useState(false);
  const [selectedStrategyId, setSelectedStrategyId] = useState('');
  const [submitError, setSubmitError] = useState('');
  const fetchCountRef = useRef(0);

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
          'VolatilityRegime': 'volatility_regime',
          'SimpleMomentum': 'simple_momentum',
          'MomentumEOD': 'momentum_eod',
          'MeanReversion1m': 'mean_reversion_1m',
          'DualThrust': 'dual_thrust'
        }).find(([, id]) => id === res.data.selected_strategy || [0] === res.data.selected_strategy);
        if (stratEntry) setSelectedStrategyId(stratEntry[1]);
      }
    } catch (err) {
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
    } catch (err) {
      console.error('Failed to fetch market data:', err);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchStrategies();
    fetchMarketData();

    const statusInterval = setInterval(fetchStatus, 3000);
    const marketInterval = setInterval(fetchMarketData, 5000);

    return () => {
      clearInterval(statusInterval);
      clearInterval(marketInterval);
    };
  }, [fetchStatus, fetchStrategies, fetchMarketData]);

  const startSystem = async () => {
    setIsLoading(true);
    setSubmitError('');
    try {
      await axios.post(`${API_BASE}/start`, { broker: selectedBroker });
      await new Promise(r => setTimeout(r, 1000));
      fetchStatus();
    } catch (err) {
      const msg = err.response?.data?.error || err.message;
      setSubmitError(msg);
    }
    setIsLoading(false);
  };

  const stopSystem = async () => {
    setIsLoading(true);
    setSubmitError('');
    try {
      await axios.post(`${API_BASE}/stop`);
      await new Promise(r => setTimeout(r, 500));
      fetchStatus();
    } catch (err) {
      const msg = err.response?.data?.error || err.message;
      setSubmitError(msg);
    }
    setIsLoading(false);
  };

  const submitOrder = async () => {
    if (!orderForm.symbol || !orderForm.quantity) return;
    setIsLoading(true);
    setSubmitError('');
    try {
      const res = await axios.post(`${API_BASE}/orders`, {
        symbol: orderForm.symbol.toUpperCase(),
        quantity: parseInt(orderForm.quantity),
        side: orderForm.side,
        broker: selectedBroker
      });
      setOrders(prev => [res.data, ...prev].slice(0, 20));
      setOrderForm({ symbol: '', quantity: '', side: 'BUY' });
      fetchStatus();
    } catch (err) {
      const msg = err.response?.data?.error || err.message;
      setSubmitError(msg);
    }
    setIsLoading(false);
  };

  const selectStrategy = async (strategyId) => {
    setSelectedStrategyId(strategyId);
    try {
      await axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategyId });
    } catch (err) {
      console.error('Failed to select strategy:', err);
    }
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
          <select
            className="broker-select"
            value={selectedBroker}
            onChange={(e) => setSelectedBroker(e.target.value)}
          >
            {BROKERS.map(b => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
          {systemStatus === 'running' ? (
            <button className="btn btn-stop" onClick={stopSystem} disabled={isLoading}>
              ■ STOP
            </button>
          ) : (
            <button className="btn btn-start" onClick={startSystem} disabled={isLoading}>
              ▶ START
            </button>
          )}
        </div>
      </header>

      {submitError && (
        <div style={{ background: 'var(--accent-red)', color: '#fff', padding: '8px 16px', fontSize: '13px' }}>
          {submitError}
        </div>
      )}

      <main className="main">
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
                      <span style={{ color: pnlColor(h.pnl || 0), fontSize: '12px' }}>
                        {formatCurrency(h.pnl || 0)}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">⚡ STRATEGY ZONE</div>
            <div className="panel-content">
              <select
                className="strategy-select"
                value={selectedStrategyId}
                onChange={(e) => selectStrategy(e.target.value)}
              >
                {availableStrategies.map(s => (
                  <option key={s.id} value={s.id}>
                    {s.name} {s.enabled ? '(Active)' : ''}
                  </option>
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
                <div className="active-strategy-meta">
                  Regime: BULL | Signals: {activeStrategies.length}
                </div>
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
                <input
                  className="order-input"
                  placeholder="Symbol"
                  value={orderForm.symbol}
                  onChange={(e) => setOrderForm(prev => ({ ...prev, symbol: e.target.value }))}
                />
                <input
                  className="order-input order-qty"
                  placeholder="Qty"
                  value={orderForm.quantity}
                  onChange={(e) => setOrderForm(prev => ({ ...prev, quantity: e.target.value }))}
                />
                <select
                  className="order-select"
                  value={orderForm.side}
                  onChange={(e) => setOrderForm(prev => ({ ...prev, side: e.target.value }))}
                >
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </div>
              <button
                className="btn btn-submit"
                onClick={submitOrder}
                disabled={isLoading || !orderForm.symbol || !orderForm.quantity}
              >
                Submit Order
              </button>
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
        </div>
      </main>
    </div>
  );
}

export default App;
