import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import BacktestDashboard from './BacktestDashboard';
import LiveTradingPage from './LiveTradingPage';
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
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('backtest');
  const [submitError, setSubmitError] = useState('');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/status`);
      setApiConnected(true);
      setSystemStatus(res.data.status);
    } catch {
      setApiConnected(false);
      setSystemStatus('stopped');
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

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
      </div>

      <main className="main">
        {activeTab === 'backtest' ? <BacktestDashboard /> : <LiveTradingPage />}
      </main>
    </div>
  );
}

export default App;
