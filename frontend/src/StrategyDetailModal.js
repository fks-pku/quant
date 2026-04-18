import React, { useState, useEffect } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';

const API_BASE = 'http://localhost:5000/api';

const fmtCurrency = (v) => {
  const n = parseFloat(v) || 0;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
};
const fmtPct = (v) => (parseFloat(v) || 0).toFixed(2) + '%';
const pnlColor = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function ModalPnlChart({ curve }) {
  if (!curve || curve.length < 2) return null;
  const W = 600, H = 120, padL = 50, padR = 15, padT = 10, padB = 20;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxAbs = Math.max(...curve.map(Math.abs), 1);
  const scaleX = (i) => padL + (i / (curve.length - 1)) * plotW;
  const scaleY = (v) => padT + plotH / 2 - (v / maxAbs) * (plotH / 2 - 4);
  const points = curve.map((v, i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  const zeroY = padT + plotH / 2;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: '120px' }}>
      <line x1={padL} y1={zeroY} x2={W - padR} y2={zeroY} stroke="#333355" strokeWidth="0.5" />
      <polyline points={points} fill="none" stroke="#00ff88" strokeWidth="1.2" />
      {[maxAbs, 0, -maxAbs].map((v, i) => (
        <text key={i} x={padL - 6} y={scaleY(v) + 3} textAnchor="end" fill="#666680" fontSize="8">
          {v === 0 ? '$0' : fmtCurrency(v)}
        </text>
      ))}
    </svg>
  );
}

export default function StrategyDetailModal({ isOpen, onClose, strategy }) {
  const [activeTab, setActiveTab] = useState('readme');
  const [readme, setReadme] = useState(null);
  const [liveData, setLiveData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isOpen || !strategy) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose, strategy]);

  useEffect(() => {
    if (!isOpen || !strategy) return;
    setActiveTab('readme');
    setReadme(null);
    setLiveData(null);

    setLoading(true);
    const fetchAll = async () => {
      const promises = [];
      promises.push(
        axios.get(`${API_BASE}/strategies/${strategy.id}/readme`)
          .then(res => setReadme(res.data)).catch(() => setReadme(null))
      );
      promises.push(
        axios.get(`${API_BASE}/strategies/performance/${strategy.id}`)
          .then(res => setLiveData(res.data)).catch(() => setLiveData(null))
      );
      await Promise.all(promises);
      setLoading(false);
    };
    fetchAll();
  }, [isOpen, strategy]);

  if (!isOpen || !strategy) return null;

  const tabs = [
    { id: 'readme', label: 'README' },
    { id: 'live', label: 'Live Performance' },
  ];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content strategy-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{strategy.name}</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="sdm-tabs">
          {tabs.map(t => (
            <button key={t.id} className={`sdm-tab ${activeTab === t.id ? 'active' : ''}`}
              onClick={() => setActiveTab(t.id)}>{t.label}</button>
          ))}
        </div>
        <div className="modal-body">
          {loading ? (
            <div className="empty-text">Loading...</div>
          ) : activeTab === 'readme' ? (
            readme && readme.content ? (
              <div className="readme-content">
                <ReactMarkdown>{readme.content}</ReactMarkdown>
              </div>
            ) : (
              <div className="empty-text">README not available</div>
            )
          ) : (
            liveData && liveData.performance ? (
              <div className="sdm-performance">
                <div className="sdm-metrics-grid">
                  {[
                    { label: 'Total P&L', value: fmtCurrency(liveData.performance.total_pnl), color: pnlColor(liveData.performance.total_pnl) },
                    { label: 'Sharpe Ratio', value: (liveData.performance.sharpe_ratio || 0).toFixed(2) },
                    { label: 'Max Drawdown', value: fmtPct(liveData.performance.max_drawdown), color: 'var(--accent-red)' },
                    { label: 'Win Rate', value: fmtPct(liveData.performance.win_rate) },
                    { label: 'Total Trades', value: liveData.performance.total_trades },
                    { label: 'Profit Factor', value: (liveData.performance.profit_factor || 0).toFixed(2) },
                  ].map((m, i) => (
                    <div key={i} className="bt-metric">
                      <div className="bt-metric-value" style={m.color ? { color: m.color } : {}}>{m.value}</div>
                      <div className="bt-metric-label">{m.label}</div>
                    </div>
                  ))}
                </div>
                {liveData.pnl_curve && liveData.pnl_curve.length > 1 && (
                  <div className="sdm-chart-section">
                    <div className="bt-chart-title">P&L Curve</div>
                    <ModalPnlChart curve={liveData.pnl_curve} />
                  </div>
                )}
                {liveData.recent_trades && liveData.recent_trades.length > 0 && (
                  <div className="sdm-trades-section">
                    <div className="bt-chart-title">Recent Trades</div>
                    <div className="bt-trades-scroll">
                      <table className="bt-trades-table">
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
                          {liveData.recent_trades.map((t, i) => (
                            <tr key={i}>
                              <td>{new Date(t.time).toLocaleDateString()}</td>
                              <td style={{ color: t.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{t.side}</td>
                              <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                              <td>{t.quantity}</td>
                              <td>${t.price}</td>
                              <td style={{ color: pnlColor(t.pnl), fontWeight: 600 }}>
                                {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-text">该策略尚未在实盘中运行</div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
