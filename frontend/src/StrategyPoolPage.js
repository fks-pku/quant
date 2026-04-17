import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import CIOAssessmentPanel from './CIOAssessmentPanel';
import StrategyWeightBar from './StrategyWeightBar';
import StrategyDetailModal from './StrategyDetailModal';
import ReactMarkdown from 'react-markdown';

const API_BASE = 'http://localhost:5000/api';

export default function StrategyPoolPage() {
  const [cioAssessment, setCioAssessment] = useState(null);
  const [strategyPool, setStrategyPool] = useState({ total_capital: 100000, strategies: [] });
  const [allStrategies, setAllStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [readmeContent, setReadmeContent] = useState(null);
  const [activationLoading, setActivationLoading] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);

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
      await axios.post(`${API_BASE}/strategies/${strategyId}/toggle`, {
        enabled: !currentEnabled
      });
      await Promise.all([fetchStrategyPool(), fetchAllStrategies()]);
    } catch (e) { 
      console.error('Strategy toggle error', e);
      alert('Failed to toggle strategy');
    }
    setActivationLoading(false);
  };

  const handleRowClick = async (strategy) => {
    setSelectedStrategy(strategy);
    setDetailOpen(true);
    
    // Fetch README
    if (strategy.has_readme) {
      try {
        const res = await axios.get(`${API_BASE}/strategies/${strategy.id}/readme`);
        setReadmeContent(res.data);
      } catch (e) { 
        console.error('README fetch error', e);
        setReadmeContent(null);
      }
    } else {
      setReadmeContent(null);
    }
  };

  const handleBacktestClick = (strategyId) => {
    // Navigate to backtest module with this strategy pre-selected
    // This could be done via a callback prop or global state
    window.dispatchEvent(new CustomEvent('navigateToBacktest', { detail: { strategyId } }));
  };

  const weights = cioAssessment?.weights || {};

  // Filter and sort activated strategies by allocated capital (descending)
  const activatedStrategies = strategyPool.strategies
    .filter(s => s.enabled)
    .sort((a, b) => (b.allocated_capital || 0) - (a.allocated_capital || 0));

  return (
    <div className="strategy-pool-page">
      <div className="sp-header">
        <h2 className="sp-title">Strategy Pool Management</h2>
        <div className="sp-subtitle">
          Total Capital: ${strategyPool.total_capital?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
        </div>
      </div>

      <CIOAssessmentPanel assessment={cioAssessment} onRefresh={handleRefresh} loading={loading} />

      {Object.keys(weights).length > 0 && (
        <div className="sp-weights-section">
          <div className="sp-section-title">CIO Weight Allocation</div>
          <StrategyWeightBar weights={weights} />
        </div>
      )}

      {/* Strategy Activation Dropdown */}
      <div className="sp-activation-section">
        <div className="sp-section-title">Strategy Activation</div>
        <div className="activation-dropdown-container">
          <button 
            className="activation-dropdown-toggle"
            onClick={() => setDropdownOpen(!dropdownOpen)}
            disabled={activationLoading}
          >
            {activationLoading ? 'Updating...' : 'Manage Strategies'}
            <span className={`dropdown-arrow ${dropdownOpen ? 'open' : ''}`}>▼</span>
          </button>
          
          {dropdownOpen && (
            <div className="activation-dropdown-menu">
              <div className="dropdown-header">
                <span>Strategy</span>
                <span>Status</span>
              </div>
              {allStrategies.map((s) => (
                <div key={s.id} className="dropdown-item">
                  <span className="dropdown-strategy-name">{s.name}</span>
                  <label className="toggle-switch">
                    <input
                      type="checkbox"
                      checked={s.enabled}
                      onChange={() => handleToggleStrategy(s.id, s.enabled)}
                    />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Activated Strategies Table */}
      <div className="sp-table-section">
        <div className="sp-section-title">
          Active Strategies ({activatedStrategies.length})
        </div>
        <div className="strategy-table-container">
          <table className="strategy-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Status</th>
                <th>Weight</th>
                <th>NAV Allocated</th>
                <th>P&L</th>
                <th>Sharpe</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {activatedStrategies.length === 0 ? (
                <tr>
                  <td colSpan="7" className="empty-row">
                    No active strategies. Use the dropdown above to activate strategies.
                  </td>
                </tr>
              ) : (
                activatedStrategies.map((s) => (
                  <tr 
                    key={s.id} 
                    className="strategy-row"
                    onClick={() => handleRowClick(s)}
                  >
                    <td className="strategy-name-cell">{s.name}</td>
                    <td>
                      <span className="status-badge active">
                        ● Active
                      </span>
                    </td>
                    <td>
                      <div className="weight-cell">
                        <div className="weight-bar-mini">
                          <div
                            className="weight-bar-mini-fill"
                            style={{ width: `${(s.weight * 100).toFixed(1)}%` }}
                          />
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
                    <td className="sharpe-cell">
                      {s.backtest_sharpe?.toFixed(2) || '-'}
                    </td>
                    <td>
                      <button 
                        className="btn-view-detail"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRowClick(s);
                        }}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Strategy Detail Modal */}
      <StrategyDetailModal
        isOpen={detailOpen}
        onClose={() => {
          setDetailOpen(false);
          setSelectedStrategy(null);
          setReadmeContent(null);
        }}
        strategy={selectedStrategy}
        readme={readmeContent}
        onBacktest={() => handleBacktestClick(selectedStrategy?.id)}
      />
    </div>
  );
}
