import React from 'react';

export default function StrategyCard({ strategy, onReadme }) {
  const {
    name, id, enabled, weight, allocated_capital,
    current_pnl, backtest_sharpe, has_readme
  } = strategy;

  const pnlColor = current_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  return (
    <div className={`strategy-card ${enabled ? '' : 'strategy-card-disabled'}`}>
      <div className="strategy-card-name">{name}</div>
      <div className="strategy-card-weight">
        <div className="weight-bar-mini">
          <div
            className="weight-bar-mini-fill"
            style={{ width: `${(weight * 100).toFixed(1)}%` }}
          />
        </div>
        <span>{weight > 0 ? `${(weight * 100).toFixed(0)}%` : 'Disabled'}</span>
      </div>
      <div className="strategy-card-capital">
        ${allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
      </div>
      <div className="strategy-card-status" style={{ color: enabled ? 'var(--accent-green)' : 'var(--text-muted)' }}>
        {enabled ? '● Active' : '○ Disabled'}
      </div>
      <div className="strategy-card-pnl" style={{ color: pnlColor }}>
        {current_pnl >= 0 ? '+' : ''}${current_pnl?.toFixed(2) || '0.00'}
      </div>
      <div className="strategy-card-sharpe">
        Sharpe: {backtest_sharpe?.toFixed(2) || '-'}
      </div>
      <div className="strategy-card-actions">
        {has_readme && (
          <button className="btn-readme" onClick={() => onReadme(id)}>
            📄 README
          </button>
        )}
      </div>
    </div>
  );
}
