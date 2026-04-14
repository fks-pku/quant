import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import './App.css';

const API_BASE = 'http://localhost:5000/api';

function App() {
  const [systemStatus, setSystemStatus] = useState('stopped');
  const [portfolio, setPortfolio] = useState({ nav: 0, total_unrealized_pnl: 0, total_realized_pnl: 0 });
  const [strategies, setStrategies] = useState([]);
  const [availableStrategies, setAvailableStrategies] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [strategyDocs, setStrategyDocs] = useState(null);
  const [strategyBacktest, setStrategyBacktest] = useState(null);
  const [positions, setPositions] = useState([]);
  const [logs, setLogs] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [showDocsModal, setShowDocsModal] = useState(false);
  const [currentRegime, setCurrentRegime] = useState('chop');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/status`);
      setSystemStatus(res.data.status);
      setPortfolio(res.data.portfolio || { nav: 0, total_unrealized_pnl: 0, total_realized_pnl: 0 });
      setStrategies(res.data.strategies || []);
      setPositions(res.data.positions || []);
    } catch (err) {
      console.error('Failed to fetch status:', err);
    }
  }, []);

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      setAvailableStrategies(res.data.strategies || []);
      setSelectedStrategy(res.data.selected);
    } catch (err) {
      console.error('Failed to fetch strategies:', err);
    }
  }, []);

  const fetchStrategyDocs = async (strategyId) => {
    try {
      const res = await axios.get(`${API_BASE}/strategies/docs/${strategyId}`);
      setStrategyDocs(res.data);
      setShowDocsModal(true);
    } catch (err) {
      console.error('Failed to fetch strategy docs:', err);
    }
  };

  const fetchStrategyBacktest = async (strategyId) => {
    try {
      const res = await axios.get(`${API_BASE}/strategies/backtest/${strategyId}`);
      setStrategyBacktest(res.data.backtest);
    } catch (err) {
      console.error('Failed to fetch backtest:', err);
    }
  };

  const handleSelectStrategy = async (strategyId) => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategyId });
      setSelectedStrategy(strategyId);
      fetchStrategyBacktest(strategyId);
    } catch (err) {
      console.error('Failed to select strategy:', err);
    }
    setIsLoading(false);
  };

  useEffect(() => {
    fetchStatus();
    fetchStrategies();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, [fetchStatus, fetchStrategies]);

  const startSystem = async () => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/start`);
      setLogs(prev => [...prev, { time: new Date().toISOString(), msg: 'System starting...', type: 'info' }]);
      fetchStatus();
    } catch (err) {
      setLogs(prev => [...prev, { time: new Date().toISOString(), msg: 'Failed to start system', type: 'error' }]);
    }
    setIsLoading(false);
  };

  const stopSystem = async () => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/stop`);
      setLogs(prev => [...prev, { time: new Date().toISOString(), msg: 'System stopping...', type: 'info' }]);
      fetchStatus();
    } catch (err) {
      setLogs(prev => [...prev, { time: new Date().toISOString(), msg: 'Failed to stop system', type: 'error' }]);
    }
    setIsLoading(false);
  };

  const formatCurrency = (val) => {
    const num = parseFloat(val) || 0;
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
  };

  const pnlColor = (val) => val >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  const getRegimeColor = (regime) => {
    switch (regime) {
      case 'bull': return 'var(--accent-green)';
      case 'bear': return 'var(--accent-red)';
      default: return 'var(--accent-amber)';
    }
  };

  return (
    <div className="app">
      {showDocsModal && strategyDocs && (
        <div className="modal-overlay" onClick={() => setShowDocsModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{strategyDocs.strategy_name} Documentation</h2>
              <button className="modal-close" onClick={() => setShowDocsModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <ReactMarkdown>{strategyDocs.content}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      <header className="header">
        <div className="header-left">
          <div className="logo">
            <span className="logo-icon">◆</span>
            <span className="logo-text">QUANT<span className="logo-accent">SYSTEM</span></span>
          </div>
          <div className="status-badge" data-status={systemStatus}>
            <span className="status-dot"></span>
            <span className="status-text">{systemStatus.toUpperCase()}</span>
          </div>
          {selectedStrategy && (
            <div className="regime-badge" style={{ borderColor: getRegimeColor(currentRegime) }}>
              <span className="regime-label">REGIME:</span>
              <span className="regime-value" style={{ color: getRegimeColor(currentRegime) }}>{currentRegime.toUpperCase()}</span>
            </div>
          )}
        </div>
        <div className="header-right">
          <span className="header-time mono">{new Date().toLocaleString()}</span>
        </div>
      </header>

      <main className="main">
        <div className="control-section">
          <div className="control-card">
            <div className="control-visual">
              <div className="orb-container">
                <div className={`orb ${systemStatus === 'running' ? 'orb-active' : ''}`}></div>
                <div className="orb-ring"></div>
                <div className="orb-ring orb-ring-2"></div>
              </div>
            </div>
            <div className="control-info">
              <h2 className="control-title">Trading Engine</h2>
              <p className="control-desc">Start or stop the quantitative trading system</p>
            </div>
            <div className="control-actions">
              {systemStatus === 'running' ? (
                <button className="btn btn-stop" onClick={stopSystem} disabled={isLoading}>
                  <span className="btn-icon">■</span>
                  <span className="btn-text">STOP SYSTEM</span>
                </button>
              ) : (
                <button className="btn btn-start" onClick={startSystem} disabled={isLoading}>
                  <span className="btn-icon">▶</span>
                  <span className="btn-text">START SYSTEM</span>
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-label">Net Asset Value</div>
            <div className="metric-value mono">{formatCurrency(portfolio.nav)}</div>
            <div className="metric-bar">
              <div className="metric-bar-fill" style={{ width: '100%' }}></div>
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Unrealized P&L</div>
            <div className="metric-value mono" style={{ color: pnlColor(portfolio.total_unrealized_pnl) }}>
              {formatCurrency(portfolio.total_unrealized_pnl)}
            </div>
            <div className="metric-bar">
              <div 
                className="metric-bar-fill" 
                style={{ 
                  width: `${Math.min(Math.abs(portfolio.total_unrealized_pnl / portfolio.nav) * 100, 100)}%`,
                  background: portfolio.total_unrealized_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'
                }}
              ></div>
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Realized P&L</div>
            <div className="metric-value mono" style={{ color: pnlColor(portfolio.total_realized_pnl) }}>
              {formatCurrency(portfolio.total_realized_pnl)}
            </div>
            <div className="metric-bar">
              <div 
                className="metric-bar-fill" 
                style={{ 
                  width: `${Math.min(Math.abs(portfolio.total_realized_pnl / portfolio.nav) * 100, 100)}%`,
                  background: portfolio.total_realized_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'
                }}
              ></div>
            </div>
          </div>
          <div className="metric-card metric-card-wide">
            <div className="metric-label">Total P&L</div>
            <div 
              className="metric-value mono large" 
              style={{ color: pnlColor(portfolio.total_unrealized_pnl + portfolio.total_realized_pnl) }}
            >
              {formatCurrency(portfolio.total_unrealized_pnl + portfolio.total_realized_pnl)}
            </div>
          </div>
        </div>

        <div className="strategy-selector-section">
          <h3 className="section-title">Select Trading Strategy</h3>
          <div className="strategy-selector-grid">
            {availableStrategies.map((strategy) => (
              <div 
                key={strategy.id}
                className={`strategy-selector-card ${selectedStrategy === strategy.id ? 'selected' : ''}`}
                onClick={() => handleSelectStrategy(strategy.id)}
              >
                <div className="strategy-selector-header">
                  <span className="strategy-selector-name">{strategy.name}</span>
                  {selectedStrategy === strategy.id && (
                    <span className="strategy-selected-badge">ACTIVE</span>
                  )}
                </div>
                <p className="strategy-selector-desc">{strategy.description}</p>
                <div className="strategy-selector-meta">
                  <span className={`strategy-sharpe ${strategy.backtest?.sharpe >= 0.8 ? 'good' : strategy.backtest?.sharpe >= 0.5 ? 'medium' : 'poor'}`}>
                    Sharpe: {strategy.backtest?.sharpe?.toFixed(2) || 'N/A'}
                  </span>
                  <span className="strategy-mdd">
                    MaxDD: {strategy.backtest?.max_dd?.toFixed(1) || 'N/A'}%
                  </span>
                </div>
                {strategy.has_docs && (
                  <button 
                    className="strategy-learn-more"
                    onClick={(e) => {
                      e.stopPropagation();
                      fetchStrategyDocs(strategy.id);
                    }}
                  >
                    Learn More
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="tabs">
          <button 
            className={`tab ${activeTab === 'dashboard' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            Dashboard
          </button>
          <button 
            className={`tab ${activeTab === 'strategies' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('strategies')}
          >
            Strategies
          </button>
          <button 
            className={`tab ${activeTab === 'backtest' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('backtest')}
          >
            Backtest
          </button>
          <button 
            className={`tab ${activeTab === 'positions' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('positions')}
          >
            Positions
          </button>
        </div>

        <div className="content-area">
          {activeTab === 'dashboard' && (
            <div className="dashboard-grid">
              <div className="panel">
                <div className="panel-header">
                  <h3>System Overview</h3>
                </div>
                <div className="panel-content">
                  <div className="info-row">
                    <span className="info-label">Mode</span>
                    <span className="info-value mono">PAPER</span>
                  </div>
                  <div className="info-row">
                    <span className="info-label">Data Provider</span>
                    <span className="info-value mono">Yahoo Finance</span>
                  </div>
                  <div className="info-row">
                    <span className="info-label">Broker</span>
                    <span className="info-value mono">Paper Trading</span>
                  </div>
                  <div className="info-row">
                    <span className="info-label">Active Strategies</span>
                    <span className="info-value mono">{strategies.filter(s => s.enabled).length}</span>
                  </div>
                  <div className="info-row">
                    <span className="info-label">Selected Strategy</span>
                    <span className="info-value mono">{selectedStrategy || 'None'}</span>
                  </div>
                </div>
              </div>
              <div className="panel">
                <div className="panel-header">
                  <h3>Recent Activity</h3>
                </div>
                <div className="panel-content logs-container">
                  {logs.length === 0 ? (
                    <div className="empty-state">No recent activity</div>
                  ) : (
                    logs.slice(-10).reverse().map((log, i) => (
                      <div key={i} className={`log-entry log-${log.type}`}>
                        <span className="log-time mono">{new Date(log.time).toLocaleTimeString()}</span>
                        <span className="log-msg">{log.msg}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'strategies' && (
            <div className="strategies-grid">
              {strategies.length === 0 ? (
                <div className="empty-state">No strategies configured</div>
              ) : (
                strategies.map((strategy, i) => (
                  <div key={i} className={`strategy-card ${strategy.enabled ? 'strategy-active' : ''}`}>
                    <div className="strategy-header">
                      <span className="strategy-name">{strategy.name}</span>
                      <span className={`strategy-status ${strategy.enabled ? 'status-on' : 'status-off'}`}>
                        {strategy.enabled ? 'ACTIVE' : 'INACTIVE'}
                      </span>
                    </div>
                    <div className="strategy-symbols">
                      {strategy.symbols?.map((s, j) => (
                        <span key={j} className="symbol-tag">{s}</span>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {activeTab === 'backtest' && (
            <div className="backtest-panel">
              <div className="panel">
                <div className="panel-header">
                  <h3>Backtest Results - {selectedStrategy || 'Select a strategy'}</h3>
                </div>
                <div className="panel-content">
                  {strategyBacktest ? (
                    <div className="backtest-results">
                      <div className="backtest-metrics">
                        <div className="backtest-metric">
                          <span className="backtest-metric-label">Sharpe Ratio (OOS)</span>
                          <span className="backtest-metric-value" style={{ color: strategyBacktest.sharpe >= 0.8 ? 'var(--accent-green)' : strategyBacktest.sharpe >= 0.5 ? 'var(--accent-amber)' : 'var(--accent-red)' }}>
                            {strategyBacktest.sharpe.toFixed(2)}
                          </span>
                        </div>
                        <div className="backtest-metric">
                          <span className="backtest-metric-label">Max Drawdown</span>
                          <span className="backtest-metric-value" style={{ color: strategyBacktest.max_dd > 20 ? 'var(--accent-red)' : 'var(--accent-amber)' }}>
                            {strategyBacktest.max_dd.toFixed(1)}%
                          </span>
                        </div>
                        <div className="backtest-metric">
                          <span className="backtest-metric-label">CAGR</span>
                          <span className="backtest-metric-value" style={{ color: strategyBacktest.cagr > 10 ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
                            {strategyBacktest.cagr.toFixed(1)}%
                          </span>
                        </div>
                        <div className="backtest-metric">
                          <span className="backtest-metric-label">Win Rate</span>
                          <span className="backtest-metric-value">
                            {strategyBacktest.win_rate.toFixed(1)}%
                          </span>
                        </div>
                      </div>
                      <div className="backtest-validation">
                        <h4>Walk-Forward Validation</h4>
                        <div className="validation-grid">
                          <div className="validation-item">
                            <span className="validation-label">Train Sharpe</span>
                            <span className="validation-value">{strategyBacktest.train_sharpe?.toFixed(2) || 'N/A'}</span>
                          </div>
                          <div className="validation-item">
                            <span className="validation-label">Test Sharpe</span>
                            <span className="validation-value">{strategyBacktest.test_sharpe?.toFixed(2) || 'N/A'}</span>
                          </div>
                          <div className="validation-item">
                            <span className="validation-label">Sharpe Degradation</span>
                            <span className="validation-value" style={{ color: strategyBacktest.sharpe_degradation < 30 ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
                              {strategyBacktest.sharpe_degradation?.toFixed(1) || 'N/A'}%
                            </span>
                          </div>
                          <div className="validation-item">
                            <span className="validation-label">% Profitable Windows</span>
                            <span className="validation-value" style={{ color: strategyBacktest.pct_profitable > 80 ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
                              {strategyBacktest.pct_profitable?.toFixed(1) || 'N/A'}%
                            </span>
                          </div>
                        </div>
                        <p className="backtest-period">Validation Period: {strategyBacktest.period}</p>
                      </div>
                    </div>
                  ) : (
                    <div className="empty-state">Select a strategy to view backtest results</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'positions' && (
            <div className="positions-table-container">
              {positions.length === 0 ? (
                <div className="empty-state">No open positions</div>
              ) : (
                <table className="positions-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Quantity</th>
                      <th>Avg Price</th>
                      <th>Current</th>
                      <th>P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((pos, i) => (
                      <tr key={i}>
                        <td className="mono">{pos.symbol}</td>
                        <td className="mono">{pos.quantity}</td>
                        <td className="mono">{formatCurrency(pos.avg_price)}</td>
                        <td className="mono">{formatCurrency(pos.current_price)}</td>
                        <td className="mono" style={{ color: pnlColor(pos.pnl) }}>
                          {formatCurrency(pos.pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </main>

      <footer className="footer">
        <span className="footer-text">Quant Trading System v1.0</span>
        <span className="footer-sep">·</span>
        <span className="footer-text">Powered by Python & React</span>
      </footer>
    </div>
  );
}

export default App;
