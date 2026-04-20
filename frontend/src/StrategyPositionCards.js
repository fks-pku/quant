import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const fmtCur = (v, market) => {
  const n = parseFloat(v) || 0;
  const prefix = market === 'US' ? '$' : 'HK$';
  return prefix + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const pnlColor = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function Sparkline({ data, width = 120, height = 32 }) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;
  const vals = data.map(d => d.nav || d.market_value || 0);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(' ');
  const isUp = vals[vals.length - 1] >= vals[0];
  const color = isUp ? 'var(--accent-green)' : 'var(--accent-red)';
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={points} />
    </svg>
  );
}

function StrategyCard({ name, data, history, holdings }) {
  const [expanded, setExpanded] = useState(false);
  const isDefault = name === 'default';
  const displayName = isDefault ? '手动交易' : name;
  const borderColor = isDefault ? 'rgba(255,255,255,0.15)' : 'var(--accent-cyan)';
  const holdingsList = data?.holdings || holdings || [];
  const mv = data?.total_market_value || 0;
  const pnl = data?.total_unrealized_pnl || 0;

  const detectMarket = (sym) => {
    if (sym.startsWith('HK.') || /^\d{5}$/.test(sym)) return 'HK';
    return 'US';
  };

  return (
    <div style={{
      flex: '1 1 280px', minWidth: '260px', maxWidth: '400px',
      background: 'var(--bg-secondary)', borderRadius: '10px',
      borderTop: `3px solid ${borderColor}`, padding: '16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontWeight: 700, fontSize: '14px', color: 'var(--text-primary)' }}>{displayName}</span>
          <span style={{
            fontSize: '10px', padding: '1px 6px', borderRadius: '3px',
            background: 'rgba(0,200,0,0.12)', color: 'var(--accent-green)',
          }}>Active</span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '10px' }}>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>市值</div>
          <div style={{ fontWeight: 600, fontSize: '13px' }}>{fmtCur(mv, detectMarket(holdingsList[0]?.symbol || ''))}</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>盈亏</div>
          <div style={{ fontWeight: 600, fontSize: '13px', color: pnlColor(pnl) }}>{fmtCur(pnl, detectMarket(holdingsList[0]?.symbol || ''))}</div>
        </div>
      </div>

      {history && history.length > 1 && (
        <div style={{ marginBottom: '10px' }}>
          <Sparkline data={history} />
        </div>
      )}

      <div
        onClick={() => setExpanded(!expanded)}
        style={{ cursor: 'pointer', fontSize: '12px', color: 'var(--accent-cyan)', userSelect: 'none' }}
      >
        {expanded ? '▾' : '▸'} 持仓详情 ({holdingsList.length})
      </div>

      {expanded && holdingsList.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px', marginTop: '8px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
              <th style={{ textAlign: 'left', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>股票</th>
              <th style={{ textAlign: 'right', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>数量</th>
              <th style={{ textAlign: 'right', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>市值</th>
              <th style={{ textAlign: 'right', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>盈亏</th>
            </tr>
          </thead>
          <tbody>
            {holdingsList.map((h, i) => (
              <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '3px 2px', fontWeight: 600 }}>{h.symbol}</td>
                <td style={{ textAlign: 'right', padding: '3px 2px' }}>{h.qty || h.quantity}</td>
                <td style={{ textAlign: 'right', padding: '3px 2px' }}>{fmtCur(h.market_value || 0, detectMarket(h.symbol))}</td>
                <td style={{ textAlign: 'right', padding: '3px 2px', color: pnlColor(h.unrealized_pnl || 0), fontWeight: 600 }}>{fmtCur(h.unrealized_pnl || 0, detectMarket(h.symbol))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function StrategyPositionCards({ broker, futuReady }) {
  const [breakdown, setBreakdown] = useState({});
  const [history, setHistory] = useState({});

  const fetchData = useCallback(async () => {
    try {
      if (broker === 'futu' && futuReady) {
        const res = await axios.get(`${API_BASE}/futu/positions`);
        if (res.data && !res.data.error) {
          setBreakdown(res.data.strategy_breakdown || {});
        }
      } else if (broker !== 'futu') {
        const res = await axios.get(`${API_BASE}/strategy-positions`);
        setBreakdown(res.data || {});
      }
    } catch { /* ignore */ }
  }, [broker, futuReady]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategy/all-history`);
      setHistory(res.data || {});
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchData();
    const i = setInterval(fetchData, 5000);
    return () => clearInterval(i);
  }, [fetchData]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const strategies = Object.keys(breakdown);
  if (strategies.length === 0) return null;

  const sorted = [...strategies].sort((a, b) => {
    if (a === 'default') return 1;
    if (b === 'default') return -1;
    return (breakdown[b].total_market_value || 0) - (breakdown[a].total_market_value || 0);
  });

  return (
    <div>
      <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--accent-cyan)', marginBottom: '10px', letterSpacing: '0.5px' }}>策略持仓</div>
      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
        {sorted.map(name => (
          <StrategyCard
            key={name}
            name={name}
            data={breakdown[name]}
            history={history[name]}
            holdings={breakdown[name]?.holdings || []}
          />
        ))}
      </div>
    </div>
  );
}
