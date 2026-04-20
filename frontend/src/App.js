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
  const [modalMsg, setModalMsg] = useState('');
  const [modalCallback, setModalCallback] = useState(null);
  const [futuRunning, setFutuRunning] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/status`);
      setApiConnected(true);
      if (selectedBroker === 'futu' && futuRunning) {
        try {
          const fs = await axios.get(`${API_BASE}/futu/status`);
          if (!fs.data.connected) setFutuRunning(false);
        } catch { setFutuRunning(false); }
      } else {
        setSystemStatus(res.data.status);
      }
    } catch {
      setApiConnected(false);
      setSystemStatus('stopped');
    }
  }, [selectedBroker, futuRunning]);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const showModal = (msg) => new Promise((resolve) => {
    setModalMsg(msg);
    setModalCallback(() => () => {
      setModalMsg('');
      setModalCallback(null);
      resolve();
    });
  });

  const startSystem = async () => {
    setIsLoading(true);
    setSubmitError('');

    if (selectedBroker === 'futu') {
      try {
        const connRes = await axios.post(`${API_BASE}/futu/connect`, {});
        if (connRes.data.connected) {
          const statusRes = await axios.get(`${API_BASE}/futu/status`);
          if (!statusRes.data.unlocked) {
            await showModal('请在 Futu OpenD GUI 中解锁交易，完成后点击确定');
            try {
              await axios.post(`${API_BASE}/futu/unlock`, {});
            } catch {}
          }
          const statusRes2 = await axios.get(`${API_BASE}/futu/status`);
          if (!statusRes2.data.unlocked) {
            setSubmitError('Futu 交易未解锁，请在 OpenD 中解锁后重试');
            setIsLoading(false);
            return;
          }
          setFutuRunning(true);
          setSystemStatus('running');
          setIsLoading(false);
          return;
        }
      } catch (err) {
        setSubmitError(err.response?.data?.error || '连接 Futu OpenD 失败，请确认 OpenD 已启动');
        setIsLoading(false);
        return;
      }
    }

    try {
      await axios.post(`${API_BASE}/start`, { broker: selectedBroker });
      await new Promise(r => setTimeout(r, 1000));
      fetchStatus();
    } catch (err) {
      setSubmitError(err.response?.data?.error || err.message);
    }
    setIsLoading(false);
  };

  const stopSystem = async () => {
    setIsLoading(true);
    setSubmitError('');
    if (selectedBroker === 'futu') {
      try { await axios.post(`${API_BASE}/futu/disconnect`, {}); } catch {}
      setFutuRunning(false);
      setSystemStatus('stopped');
      setIsLoading(false);
      return;
    }
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
          <select className="broker-select" value={selectedBroker} onChange={(e) => { setSelectedBroker(e.target.value); setSubmitError(''); if (futuRunning) { setFutuRunning(false); setSystemStatus('stopped'); } }}>
            {BROKERS.map(b => (<option key={b.id} value={b.id}>{b.name}</option>))}
          </select>
          {systemStatus === 'running' ? (
            <button className="btn btn-stop" onClick={stopSystem} disabled={isLoading}>■ STOP</button>
          ) : (
            <button className="btn btn-start" onClick={startSystem} disabled={isLoading}>
              {isLoading ? '...' : '▶ START'}
            </button>
          )}
        </div>
      </header>

      {submitError && (
        <div style={{ background: 'var(--accent-red)', color: '#fff', padding: '8px 16px', fontSize: '13px' }}>{submitError}</div>
      )}

      {modalMsg && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 }}>
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--accent-cyan)', borderRadius: '10px', padding: '28px 36px', maxWidth: '420px', textAlign: 'center' }}>
            <div style={{ fontSize: '15px', color: 'var(--text-primary)', marginBottom: '20px', lineHeight: 1.6 }}>{modalMsg}</div>
            <button className="btn btn-start" style={{ padding: '8px 32px' }} onClick={modalCallback}>确定</button>
          </div>
        </div>
      )}

      <div className="tab-bar">
        <button className={`tab ${activeTab === 'backtest' ? 'active' : ''}`} onClick={() => setActiveTab('backtest')}>BACKTEST</button>
        <button className={`tab ${activeTab === 'live' ? 'active' : ''}`} onClick={() => setActiveTab('live')}>LIVE TRADING</button>
      </div>

      <main className="main">
        {activeTab === 'backtest' ? <BacktestDashboard /> : <LiveTradingPage broker={selectedBroker} systemRunning={systemStatus === 'running'} />}
      </main>
    </div>
  );
}

export default App;
