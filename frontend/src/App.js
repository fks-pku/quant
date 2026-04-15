import React, { useState, useEffect, useCallback } from 'react';
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
  const [portfolio, setPortfolio] = useState({
    nav: 100000,
    total_unrealized_pnl: 0,
    total_realized_pnl: 0,
    total_pnl: 0,
    holdings: []
  });
  const [availableStrategies, setAvailableStrategies] = useState([]);
  const [activeStrategies, setActiveStrategies] = useState([]);
  const [marketData, setMarketData] = useState([
    { symbol: 'AAPL', price: 178.50, change: 1.2 },
    { symbol: 'MSFT', price: 378.25, change: -0.5 },
    { symbol: 'VIX', price: 14.5, sma: 16.2 },
  ]);
  const [orders, setOrders] = useState([]);
  const [orderForm, setOrderForm] = useState({ symbol: '', quantity: '', side: 'BUY' });
  const [isLoading, setIsLoading] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/status`);
      setSystemStatus(res.data.status);
      setPortfolio({
        nav: res.data.portfolio?.nav || 100000,
        total_unrealized_pnl: res.data.portfolio?.total_unrealized_pnl || 0,
        total_realized_pnl: res.data.portfolio?.total_realized_pnl || 0,
        total_pnl: (res.data.portfolio?.total_unrealized_pnl || 0) + (res.data.portfolio?.total_realized_pnl || 0),
        holdings: res.data.positions || []
      });
      setActiveStrategies(res.data.strategies || []);
    } catch (err) {
      console.error('Failed to fetch status:', err);
    }
  }, []);

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      setAvailableStrategies(res.data.strategies || []);
    } catch (err) {
      console.error('Failed to fetch strategies:', err);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchStrategies();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus, fetchStrategies]);

  const startSystem = async () => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/start`, { broker: selectedBroker });
      fetchStatus();
    } catch (err) {
      console.error('Failed to start system:', err);
    }
    setIsLoading(false);
  };

  const stopSystem = async () => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/stop`);
      fetchStatus();
    } catch (err) {
      console.error('Failed to stop system:', err);
    }
    setIsLoading(false);
  };

  const submitOrder = async () => {
    if (!orderForm.symbol || !orderForm.quantity) return;
    setIsLoading(true);
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
      console.error('Failed to submit order:', err);
    }
    setIsLoading(false);
  };

  const formatCurrency = (val) => {
    const num = parseFloat(val) || 0;
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
  };

  const pnlColor = (val) => val >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  const getStatusBadge = () => {
    switch (systemStatus) {
      case 'running': return { text: 'CONNECTED', color: 'var(--accent-green)' };
      case 'starting': return { text: 'STARTING', color: 'var(--accent-amber)' };
      case 'stopping': return { text: 'STOPPING', color: 'var(--accent-amber)' };
      default: return { text: 'DISCONNECTED', color: 'var(--text-muted)' };
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

      <main className="main">
        <div className="panel-grid">
          {/* Panel 1: Asset Overview */}
          <div className="panel">
            <div className="panel-header">📊 Asset Overview</div>
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
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Panel 2: Strategy Zone */}
          <div className="panel">
            <div className="panel-header">⚡ Strategy Zone</div>
            <div className="panel-content">
              <select
                className="strategy-select"
                value={availableStrategies.find(s => s.enabled)?.id || ''}
                onChange={(e) => {
                  const strategy = availableStrategies.find(s => s.id === e.target.value);
                  if (strategy) {
                    axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategy.id });
                  }
                }}
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
                  <span className="status-running">● Running</span>
                </div>
                <div className="active-strategy-name">
                  {availableStrategies.find(s => s.enabled)?.name || 'None'}
                </div>
                <div className="active-strategy-meta">
                  Regime: BULL | Signals: {activeStrategies.length}
                </div>
              </div>
            </div>
          </div>

          {/* Panel 3: Market Data */}
          <div className="panel">
            <div className="panel-header">📈 Market Data</div>
            <div className="panel-content">
              {marketData.map((m, i) => (
                <div key={i} className="market-item">
                  <span className="market-symbol">{m.symbol}</span>
                  <span className="market-price">${m.price}</span>
                  <span className="market-change" style={{ color: m.change >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                    {m.change >= 0 ? '+' : ''}{m.change}%
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Panel 4: Order Zone */}
          <div className="panel">
            <div className="panel-header">📝 Order Zone</div>
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
                      <span>{o.side} {o.symbol} x{o.quantity}</span>
                      <span className="order-status" style={{ color: 'var(--accent-green)' }}>FILLED</span>
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