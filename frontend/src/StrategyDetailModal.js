import React, { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';

export default function StrategyDetailModal({ isOpen, onClose, strategy, readme, onBacktest }) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  if (!isOpen || !strategy) return null;

  const { backtest } = strategy;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content strategy-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{strategy.name}</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        
        <div className="modal-body">
          {/* Strategy Overview */}
          <div className="detail-section">
            <h3>Strategy Overview</h3>
            <div className="strategy-meta">
              <div className="meta-item">
                <span className="meta-label">ID:</span>
                <span className="meta-value">{strategy.id}</span>
              </div>
              <div className="meta-item">
                <span className="meta-label">Status:</span>
                <span className={`meta-value status-${strategy.enabled ? 'active' : 'inactive'}`}>
                  {strategy.enabled ? '● Active' : '○ Inactive'}
                </span>
              </div>
              <div className="meta-item">
                <span className="meta-label">Weight:</span>
                <span className="meta-value">{(strategy.weight * 100).toFixed(1)}%</span>
              </div>
              <div className="meta-item">
                <span className="meta-label">Allocated:</span>
                <span className="meta-value">
                  ${strategy.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
                </span>
              </div>
            </div>
          </div>

          {/* README / Strategy Logic */}
          {readme && (
            <div className="detail-section">
              <h3>Strategy Logic</h3>
              <div className="readme-content">
                <ReactMarkdown>{readme.content}</ReactMarkdown>
              </div>
            </div>
          )}

          {/* Key Parameters */}
          {strategy.parameters && Object.keys(strategy.parameters).length > 0 && (
            <div className="detail-section">
              <h3>Key Parameters</h3>
              <div className="parameters-grid">
                {Object.entries(strategy.parameters).map(([key, value]) => (
                  <div key={key} className="parameter-item">
                    <span className="param-name">{key.replace(/_/g, ' ')}</span>
                    <span className="param-value">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Backtest Results */}
          {backtest && (
            <div className="detail-section">
              <h3>Backtest Results</h3>
              <div className="backtest-metrics">
                <div className="metric-card">
                  <span className="metric-label">Sharpe Ratio</span>
                  <span className="metric-value">{backtest.sharpe?.toFixed(2) || '-'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Test Sharpe</span>
                  <span className="metric-value">{backtest.test_sharpe?.toFixed(2) || '-'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Max Drawdown</span>
                  <span className="metric-value">{backtest.max_dd?.toFixed(1) || '-'}%</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">CAGR</span>
                  <span className="metric-value">{backtest.cagr?.toFixed(1) || '-'}%</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Win Rate</span>
                  <span className="metric-value">{backtest.win_rate?.toFixed(1) || '-'}%</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Profitable</span>
                  <span className="metric-value">{backtest.pct_profitable?.toFixed(1) || '-'}%</span>
                </div>
              </div>
              {backtest.period && (
                <div className="backtest-period">
                  <span className="period-label">Period:</span>
                  <span className="period-value">{backtest.period}</span>
                </div>
              )}
            </div>
          )}

          {/* Online Results (if activated) */}
          {strategy.enabled && (
            <div className="detail-section">
              <h3>Live Performance</h3>
              <div className="live-metrics">
                <div className="metric-card">
                  <span className="metric-label">Current P&L</span>
                  <span className={`metric-value ${strategy.current_pnl >= 0 ? 'positive' : 'negative'}`}>
                    {strategy.current_pnl >= 0 ? '+' : ''}${strategy.current_pnl?.toFixed(2) || '0.00'}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Allocated Capital</span>
                  <span className="metric-value">
                    ${strategy.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Weight</span>
                  <span className="metric-value">{(strategy.weight * 100).toFixed(1)}%</span>
                </div>
              </div>
              
              {/* Placeholder for performance curve */}
              <div className="performance-curve">
                <div className="curve-placeholder">
                  <span>Performance curve visualization</span>
                  <small>Live equity curve will be displayed here</small>
                </div>
              </div>
            </div>
          )}

          {/* Action Buttons */}
          <div className="detail-actions">
            <button className="btn-backtest" onClick={onBacktest}>
              Run Backtest
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
