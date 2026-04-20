import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const fmtHKD = (v) => { const n = parseFloat(v) || 0; return 'HK$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
const fmtUSD = (v) => { const n = parseFloat(v) || 0; return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
const fmtCur = (v, c) => c === 'HKD' ? fmtHKD(v) : fmtUSD(v);
const pnlC = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function MarketPanel({ title, currency, holdings, cash, assets, buyingPower }) {
  const fmt = (v) => fmtCur(v, currency);
  const totalUpl = holdings.reduce((s, h) => s + (h.unrealized_pnl || 0), 0);
  const totalMv = holdings.reduce((s, h) => s + (h.market_value || 0), 0);
  const todayBuy = holdings.reduce((s, h) => s + (h.today_buy_qty || 0), 0);
  const todaySell = holdings.reduce((s, h) => s + (h.today_sell_qty || 0), 0);
  const borderColor = currency === 'HKD' ? '#ffd700' : '#0096ff';
  const tagBg = currency === 'HKD' ? 'rgba(255,215,0,0.12)' : 'rgba(0,150,255,0.12)';

  return (
    <div style={{ flex: 1, minWidth: 0, background: 'var(--bg-secondary)', borderRadius: '10px', padding: '16px', borderTop: `3px solid ${borderColor}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <span style={{ fontWeight: 700, fontSize: '14px', color: borderColor }}>{title}</span>
        <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '4px', background: tagBg, color: borderColor }}>{currency}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '14px' }}>
        <div><div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>市值</div><div style={{ fontWeight: 600 }}>{fmt(totalMv)}</div></div>
        <div><div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>现金</div><div style={{ fontWeight: 600 }}>{fmt(cash)}</div></div>
        <div><div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>持仓盈亏</div><div style={{ fontWeight: 600, color: pnlC(totalUpl) }}>{fmt(totalUpl)}</div></div>
        <div><div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>购买力</div><div style={{ fontWeight: 600 }}>{fmt(buyingPower)}</div></div>
      </div>
      {holdings.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
              <th style={{ textAlign: 'left', padding: '4px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>股票</th>
              <th style={{ textAlign: 'right', padding: '4px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>数量</th>
              <th style={{ textAlign: 'right', padding: '4px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>市值</th>
              <th style={{ textAlign: 'right', padding: '4px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>盈亏</th>
              <th style={{ textAlign: 'right', padding: '4px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>盈亏%</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((h, i) => {
              const pnl = h.unrealized_pnl || 0;
              const pct = h.pnl_pct || 0;
              return (
                <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={{ padding: '5px 2px' }}>
                    <div style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: '12px' }}>{h.symbol}</div>
                    <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>{h.stock_name || ''}</div>
                  </td>
                  <td style={{ textAlign: 'right', padding: '5px 2px' }}>{h.quantity}</td>
                  <td style={{ textAlign: 'right', padding: '5px 2px' }}>{fmt(h.market_value || 0)}</td>
                  <td style={{ textAlign: 'right', padding: '5px 2px', color: pnlC(pnl), fontWeight: 600 }}>{fmt(pnl)}</td>
                  <td style={{ textAlign: 'right', padding: '5px 2px', color: pnlC(pnl) }}>{(pct >= 0 ? '+' : '') + pct.toFixed(2)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <div style={{ color: 'var(--text-muted)', fontSize: '12px', textAlign: 'center', padding: '20px 0' }}>无持仓</div>
      )}
    </div>
  );
}

export default function AccountOverview({ broker, futuReady }) {
  const [futuData, setFutuData] = useState(null);
  const [portfolio, setPortfolio] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      if (broker === 'futu' && futuReady) {
        const res = await axios.get(`${API_BASE}/futu/positions`);
        if (res.data && !res.data.error) {
          setFutuData(res.data);
          setPortfolio({ nav: res.data.nav, total_unrealized_pnl: res.data.total_unrealized_pnl, holdings: res.data.holdings || [] });
        }
      } else if (broker !== 'futu') {
        const res = await axios.get(`${API_BASE}/portfolio`);
        setPortfolio(res.data);
      }
    } catch { /* ignore */ }
  }, [broker, futuReady]);

  useEffect(() => {
    fetchData();
    const i = setInterval(fetchData, 5000);
    return () => clearInterval(i);
  }, [fetchData]);

  const isFutu = broker === 'futu';
  const acc = futuData?.account || {};
  const holdings = portfolio?.holdings || [];
  const hkHoldings = holdings.filter(h => h.market === 'HK');
  const usHoldings = holdings.filter(h => h.market === 'US');
  const hk = futuData?.hk || {};
  const us = futuData?.us || {};

  return (
    <div>
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '16px' }}>
        <div className="po-summary-card"><div className="po-summary-label">资产净值</div><div className="po-summary-value" style={{ fontSize: '20px' }}>{fmtHKD(acc.total_assets || portfolio?.nav || 0)}</div></div>
        <div className="po-summary-card"><div className="po-summary-label">最大购买力</div><div className="po-summary-value">{fmtHKD(acc.power || acc.buying_power || 0)}</div></div>
        <div className="po-summary-card"><div className="po-summary-label">剩余流动性</div><div className="po-summary-value">{fmtHKD(acc.available_funds || acc.cash || 0)}</div></div>
        <div className="po-summary-card"><div className="po-summary-label">证券市值</div><div className="po-summary-value">{fmtHKD(acc.securities_assets || acc.market_val || 0)}</div></div>
        <div className="po-summary-card"><div className="po-summary-label">持仓盈亏</div><div className="po-summary-value" style={{ color: pnlC(acc.unrealized_pl || 0) }}>{fmtHKD(acc.unrealized_pl || 0)}</div></div>
      </div>

      {isFutu ? (
        <div style={{ display: 'flex', gap: '14px' }}>
          <MarketPanel title="港股" currency="HKD" holdings={hkHoldings} cash={hk.cash} assets={hk.assets} buyingPower={hk.buying_power} />
          <MarketPanel title="美股" currency="USD" holdings={usHoldings} cash={us.cash} assets={us.assets} buyingPower={us.buying_power} />
        </div>
      ) : (
        <div style={{ background: 'var(--bg-secondary)', borderRadius: '10px', padding: '16px' }}>
          <div style={{ fontWeight: 700, fontSize: '14px', color: 'var(--accent-cyan)', marginBottom: '12px' }}>持仓</div>
          {holdings.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '12px', textAlign: 'center', padding: '20px 0' }}>No positions</div>
          ) : (
            <table className="po-table"><thead><tr><th>Symbol</th><th>Qty</th><th>Market Value</th><th>P&L</th><th>P&L %</th></tr></thead>
            <tbody>{holdings.map((h, i) => (
              <tr key={i}><td style={{ fontWeight: 600 }}>{h.symbol}</td><td>{h.quantity}</td><td>${(h.market_value || 0).toLocaleString()}</td>
              <td style={{ color: pnlC(h.pnl || 0), fontWeight: 600 }}>${(h.pnl || 0).toFixed(2)}</td><td style={{ color: pnlC(h.pnl || 0) }}>{((h.pnl_pct || 0) >= 0 ? '+' : '') + (h.pnl_pct || 0).toFixed(2)}%</td></tr>
            ))}</tbody></table>
          )}
        </div>
      )}
    </div>
  );
}
