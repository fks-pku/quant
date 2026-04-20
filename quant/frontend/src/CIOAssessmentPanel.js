import React from 'react';

const ENV_LABELS = {
  low_vol_bull: { label: 'Low Vol Bull', color: 'var(--accent-green)' },
  medium_vol_chop: { label: 'Medium Volatility', color: 'var(--accent-amber)' },
  high_vol_bear: { label: 'High Vol Bear', color: 'var(--accent-red)' },
};

export default function CIOAssessmentPanel({ assessment, onRefresh, loading }) {
  if (!assessment) {
    return (
      <div className="cio-panel">
        <div className="empty-text">Loading CIO assessment...</div>
      </div>
    );
  }

  const env = ENV_LABELS[assessment.environment] || { label: assessment.environment, color: 'var(--accent-cyan)' };

  return (
    <div className="cio-panel">
      <div className="cio-panel-header">
        <span className="cio-label">CIO Market Assessment</span>
        <button className="btn-refresh" onClick={onRefresh} disabled={loading}>
          {loading ? 'Refreshing...' : '↻ Refresh'}
        </button>
      </div>
      <div className="cio-metrics">
        <div className="cio-metric">
          <span className="cio-metric-label">Environment</span>
          <span className="cio-metric-value" style={{ color: env.color }}>{env.label}</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Score</span>
          <span className="cio-metric-value">{assessment.score}/100</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Sentiment</span>
          <span className="cio-metric-value" style={{
            color: assessment.sentiment === 'bullish' ? 'var(--accent-green)' :
                   assessment.sentiment === 'bearish' ? 'var(--accent-red)' : 'var(--accent-amber)'
          }}>
            {assessment.sentiment}
          </span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">VIX</span>
          <span className="cio-metric-value">{assessment.indicators?.vix || '-'}</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Trend</span>
          <span className="cio-metric-value">{assessment.indicators?.trend_strength || '-'}</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Updated</span>
          <span className="cio-metric-value" style={{ fontSize: '11px' }}>
            {assessment.last_updated ? new Date(assessment.last_updated).toLocaleTimeString() : '-'}
          </span>
        </div>
      </div>
      {assessment.llm_summary && (
        <div className="cio-summary">{assessment.llm_summary}</div>
      )}
    </div>
  );
}
