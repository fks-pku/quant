import React from 'react';

export default function StrategyWeightBar({ weights }) {
  if (!weights || Object.keys(weights).length === 0) return null;

  const colors = ['var(--accent-cyan)', 'var(--accent-green)', 'var(--accent-amber)', 'var(--accent-red)'];
  const entries = Object.entries(weights).filter(([, v]) => v > 0);

  return (
    <div className="weight-bar-container">
      <div className="weight-bar-track">
        {entries.map(([key, val], i) => (
          <div
            key={key}
            style={{
              width: `${(val * 100).toFixed(1)}%`,
              background: colors[i % colors.length],
              height: '100%',
              transition: 'width 0.3s ease',
            }}
          />
        ))}
      </div>
      <div className="weight-bar-legend">
        {entries.map(([key, val], i) => (
          <span key={key} className="weight-legend-item">
            <span style={{ color: colors[i % colors.length] }}>█</span>
            {' '}{key.replace(/_/g, ' ')} {val > 0 ? `${(val * 100).toFixed(0)}%` : ''}
          </span>
        ))}
      </div>
    </div>
  );
}
