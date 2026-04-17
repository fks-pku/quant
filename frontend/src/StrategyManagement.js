import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import CIOAssessmentPanel from './CIOAssessmentPanel';
import StrategyWeightBar from './StrategyWeightBar';
import StrategyDetailModal from './StrategyDetailModal';

const API_BASE = 'http://localhost:5000/api';

export default function StrategyManagement({ onStrategySelect }) {
  const [cioAssessment, setCioAssessment] = useState(null);
  const [strategyPool, setStrategyPool] = useState({ total_capital: 100000, strategies: [] });
  const [allStrategies, setAllStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activationLoading, setActivationLoading] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const fetchCIO = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/cio/assessment`);
      setCioAssessment(res.data);
    } catch (e) { console.error('CIO fetch error', e); }
  }, []);

  const fetchStrategyPool = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategy-pool`);
      setStrategyPool(res.data);
    } catch (e) { console.error('Strategy pool fetch error', e); }
  }, []);

  const fetchAllStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      setAllStrategies(res.data.strategies || []);
    } catch (e) { console.error('All strategies fetch error', e); }
  }, []);

  useEffect(() => {
    fetchCIO();
    fetchStrategyPool();
    fetchAllStrategies();
    const cioInterval = setInterval(fetchCIO, 60000);
    const poolInterval = setInterval(fetchStrategyPool, 5000);
    return () => {
      clearInterval(cioInterval);
      clearInterval(poolInterval);
    };
  }, [fetchCIO, fetchStrategyPool, fetchAllStrategies]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/cio/refresh`);
      await fetchCIO();
    } catch (e) { console.error('CIO refresh error', e); }
    setLoading(false);
  };

  const handleToggleStrategy = async (strategyId, currentEnabled) => {
    setActivationLoading(true);
    try {
      await axios.post(`${API_BASE}/strategies/${strategyId}/toggle`, { enabled: !currentEnabled });
      await Promise.all([fetchStrategyPool(), fetchAllStrategies()]);
    } catch (e) {
      console.error('Strategy toggle error', e);
    }
    setActivationLoading(false);
  };

  const handleSelectStrategy = async (strategyId) => {
    try {
      await axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategyId });
      if (onStrategySelect) onStrategySelect(strategyId);
      await Promise.all([fetchStrategyPool(), fetchAllStrategies()]);
    } catch (e) { console.error('Strategy select error', e); }
  };

  const handleRowClick = (strategy) => {
    setSelectedStrategy(strategy);
    setDetailOpen(true);
  };

  const weights = cioAssessment?.weights || {};
  const activatedStrategies = strategyPool.strategies
    .filter(s => s.enabled)
    .sort((a, b) => (b.allocated_capital || 0) - (a.allocated_capital || 0));

  return (
    <div className="strategy-management">
      <CIOAssessmentPanel assessment={cioAssessment} onRefresh={handleRefresh} loading={loading} />

      {Object.keys(weights).length > 0 && (
        <div className="sm-weights-section">
          <div className="sp-section-title">CIO Weight Allocation</div>
          <StrategyWeightBar weights={weights} />
        </div>
      )}

      <div className="sm-strategy-section">
        <div className="sm-section-header">
          <div className="sp-section-title">Strategy Activation</div>
          <select className="sm-activate-select" onChange={e => handleSelectStrategy(e.target.value)} defaultValue="">
            <option value="" disabled>Select strategy to activate...</option>
            {allStrategies.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>

        <div className="strategy-table-container">
          <table className="strategy-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Status</th>
                <th>Weight</th>
                <th>Allocated</th>
                <th>P&L</th>
                <th>Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {activatedStrategies.length === 0 ? (
                <tr>
                  <td colSpan="6" className="empty-row">
                    No active strategies. Select a strategy above to activate.
                  </td>
                </tr>
              ) : (
                activatedStrategies.map((s) => (
                  <tr key={s.id} className="strategy-row" onClick={() => handleRowClick(s)}>
                    <td className="strategy-name-cell">{s.name}</td>
                    <td>
                      <label className="toggle-switch" onClick={e => e.stopPropagation()}>
                        <input type="checkbox" checked={s.enabled}
                          onChange={() => handleToggleStrategy(s.id, s.enabled)}
                          disabled={activationLoading}
                        />
                        <span className="toggle-slider"></span>
                      </label>
                    </td>
                    <td>
                      <div className="weight-cell">
                        <div className="weight-bar-mini">
                          <div className="weight-bar-mini-fill" style={{ width: `${(s.weight * 100).toFixed(1)}%` }} />
                        </div>
                        <span>{(s.weight * 100).toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className="capital-cell">
                      ${s.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
                    </td>
                    <td className={`pnl-cell ${s.current_pnl >= 0 ? 'positive' : 'negative'}`}>
                      {s.current_pnl >= 0 ? '+' : ''}${s.current_pnl?.toFixed(2) || '0.00'}
                    </td>
                    <td className="sharpe-cell">{s.backtest_sharpe?.toFixed(2) || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <StrategyDetailModal
        isOpen={detailOpen}
        onClose={() => { setDetailOpen(false); setSelectedStrategy(null); }}
        strategy={selectedStrategy}
      />
    </div>
  );
}
