import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const fmtCurrency = (v) => {
  const n = parseFloat(v) || 0;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
};
const pnlColor = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

export default function PositionOverview() {
  const [portfolio, setPortfolio] = useState(null);
  const [strategyPool, setStrategyPool] = useState(null);
  const [activeTab, setActiveTab] = useState('holdings');

  const fetchData = useCallback(async () => {
    try {
      const [portRes, poolRes] = await Promise.all([
        axios.get(`${API_BASE}/portfolio`),
        axios.get(`${API_BASE}/strategy-pool`),
      ]);
      setPortfolio(portRes.data);
      setStrategyPool(poolRes.data);
    } catch (e) { console.error('PositionOverview fetch error', e); }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const nav = portfolio?.nav || 0;
  const unrealized = portfolio?.total_unrealized_pnl || 0;
  const realized = portfolio?.total_realized_pnl || 0;
  const totalPnl = unrealized + realized;
  const holdings = portfolio?.holdings || [];
  const strategies = strategyPool?.strategies || [];

  return (
    <div className="position-overview">
      <div className="po-summary">
        <div className="po-summary-card">
          <div className="po-summary-label">Total NAV</div>
          <div className="po-summary-value">{fmtCurrency(nav)}</div>
        </div>
        <div className="po-summary-card">
          <div className="po-summary-label">Unrealized P&L</div>
          <div className="po-summary-value" style={{ color: pnlColor(unrealized) }}>{fmtCurrency(unrealized)}</div>
        </div>
        <div className="po-summary-card">
          <div className="po-summary-label">Realized P&L</div>
          <div className="po-summary-value" style={{ color: pnlColor(realized) }}>{fmtCurrency(realized)}</div>
        </div>
        <div className="po-summary-card">
          <div className="po-summary-label">Total P&L</div>
          <div className="po-summary-value" style={{ color: pnlColor(totalPnl) }}>{fmtCurrency(totalPnl)}</div>
        </div>
      </div>

      <div className="po-tabs">
        <button className={`po-tab ${activeTab === 'holdings' ? 'active' : ''}`} onClick={() => setActiveTab('holdings')}>
          Securities Holdings
        </button>
        <button className={`po-tab ${activeTab === 'strategy_nav' ? 'active' : ''}`} onClick={() => setActiveTab('strategy_nav')}>
          Strategy NAV Distribution
        </button>
      </div>

      <div className="po-table-wrap">
        {activeTab === 'holdings' ? (
          holdings.length === 0 ? (
            <div className="empty-text">No positions</div>
          ) : (
            <table className="po-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Qty</th>
                  <th>Avg Cost</th>
                  <th>Market Value</th>
                  <th>Unrealized P&L</th>
                  <th>P&L %</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{h.symbol}</td>
                    <td>{h.quantity}</td>
                    <td>{fmtCurrency(h.avg_cost || h.cost_basis / h.quantity || 0)}</td>
                    <td>{fmtCurrency(h.market_value || 0)}</td>
                    <td style={{ color: pnlColor(h.pnl || 0), fontWeight: 600 }}>{fmtCurrency(h.pnl || 0)}</td>
                    <td style={{ color: pnlColor(h.pnl || 0) }}>
                      {h.market_value ? ((h.pnl / (h.market_value - (h.pnl || 0))) * 100).toFixed(2) + '%' : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          strategies.length === 0 ? (
            <div className="empty-text">No strategy data</div>
          ) : (
            <table className="po-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Status</th>
                  <th>Weight</th>
                  <th>Allocated Capital</th>
                  <th>Strategy NAV</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s.id}>
                    <td style={{ fontWeight: 600, color: 'var(--accent-cyan)' }}>{s.name}</td>
                    <td>
                      <span className={`status-badge ${s.enabled ? 'active' : 'inactive'}`}>
                        {s.enabled ? '● Active' : '○ Inactive'}
                      </span>
                    </td>
                    <td>{(s.weight * 100).toFixed(1)}%</td>
                    <td className="capital-cell">${s.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}</td>
                    <td>${s.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}</td>
                    <td className={`pnl-cell ${s.current_pnl >= 0 ? 'positive' : 'negative'}`}>
                      {s.current_pnl >= 0 ? '+' : ''}${s.current_pnl?.toFixed(2) || '0.00'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  );
}
